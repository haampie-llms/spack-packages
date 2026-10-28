#!/usr/bin/env spack-python
#
# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

"""Strictly validate the tarballs of versions added between two git refs.

Usage: verify-tarballs.py <from-ref> <to-ref>

This complements ``spack ci verify-versions``, which checks that the checksums
in package.py match the downloads. Each added version is fetched, checked
against its sha256, and extracted with ``untar`` into a temporary dir. untar
rejects archives that tar implementations parse differently, unsafe paths,
duplicate members, device files, truncation and trailing data, so a tarball
that passes extracts the same everywhere.

Downloads that are not tarballs, such as zip files or single files, are skipped,
and so are versions with ``expand=False``.
"""

import argparse
import bz2
import lzma
import os
import sys
import tempfile
from typing import IO

import untar

import spack.ci
import spack.config
import spack.error
import spack.fetch_strategy
import spack.package_base
import spack.repo
import spack.spec
import spack.stage
import spack.util.filesystem as fs
from spack.util import tty
from spack.util.compression import extension_from_magic_numbers

#: Extensions, as spack determines them, of the tarballs that are validated
TARBALL_EXTENSIONS = ("tar", "tar.gz", "tgz", "tar.bz2", "tbz2", "tar.xz", "txz")

#: Errors of decompressing and extracting an invalid archive
ARCHIVE_ERRORS = (untar.Error, OSError, EOFError, lzma.LZMAError)


def open_tar(path: str) -> IO[bytes]:
    """Open ``path`` for untar, decompressed if it is bzip2 or xz. untar decompresses gzip
    itself, and verifies its checksum."""
    with open(path, "rb") as f:
        magic = f.read(6)
    if magic.startswith(b"BZh"):
        return bz2.open(path, "rb")
    if magic == b"\xfd7zXZ\x00":
        return lzma.open(path, "rb")
    return open(path, "rb")


def verify(pkg: spack.package_base.PackageBase, version, sha256: str) -> bool:
    """Fetch the tarball of ``version``, check its sha256 and extract it with untar."""
    label = f"{pkg.name}@{version}"
    url = pkg.find_valid_url_for_version(version)
    if url is None:
        tty.error(f"No valid URLs found for {label}")
        return False

    fetcher = spack.fetch_strategy.URLFetchStrategy(
        url=url, checksum=sha256, fetch_options=pkg.fetch_options
    )
    with spack.stage.stage_from_config(fetcher, config=spack.config.CONFIG) as stage:
        try:
            stage.fetch()
            stage.check()
        except spack.error.FetchError as e:
            tty.error(f"Could not fetch {label}: {e}")
            return False

        archive = stage.archive_file
        assert archive is not None, f"Archive not found for {url}"
        # Like spack, classify the archive by its magic, and by its name for old tarballs
        extension = extension_from_magic_numbers(archive, decompress=True)
        if extension not in TARBALL_EXTENSIONS:
            tty.info(f"Skipped {label}: {os.path.basename(url)} is not a tarball")
            return True

        with tempfile.TemporaryDirectory() as tmp, open_tar(archive) as f:
            try:
                untar.extract(f, os.path.join(tmp, "src"))
            except ARCHIVE_ERRORS as e:
                tty.error(f"Invalid tarball {label}: {e}\n    {url}")
                return False

    tty.info(f"Validated {label} --> {os.path.basename(url)}")
    return True


def main():
    parser = argparse.ArgumentParser(description="strictly validate tarballs of added versions")
    parser.add_argument("from_ref", help="git ref to compare against")
    parser.add_argument("to_ref", help="git ref with the added versions")
    args = parser.parse_args()

    pkgs = spack.repo.get_all_package_diffs(
        "AC", spack.repo.builtin_repo(), args.from_ref, args.to_ref
    )

    success = True
    for name in sorted(pkgs):
        pkg = spack.repo.PATH.get_pkg_class(name)(spack.spec.Spec(name))
        if pkg.manual_download:
            tty.warn(f"Skipping manual download package: {name}")
            continue

        checksums = {
            version: info["sha256"]
            for version, info in pkg.versions.items()
            if "sha256" in info and info.get("expand", True)
        }
        path = spack.repo.PATH.package_path(name)
        with fs.working_dir(os.path.dirname(path)):
            added = spack.ci.filter_added_checksums(
                checksums.values(), path, from_ref=args.from_ref, to_ref=args.to_ref
            )

        for version, sha256 in checksums.items():
            if sha256 in added:
                success &= verify(pkg, version, sha256)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
