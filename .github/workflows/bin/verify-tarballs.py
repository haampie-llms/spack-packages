#!/usr/bin/env spack-python
#
# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

"""Strictly validate the tarballs of versions added between two git refs.

Each added version is fetched, checked against its sha256, and extracted with ``untar``, which
rejects archives that tar implementations parse differently. Downloads that are not tarballs and
versions with ``expand=False`` are skipped.
"""

import argparse
import bz2
import lzma
import os
import sys

import untar

import spack.config
import spack.error
import spack.fetch_strategy
import spack.package_base
import spack.repo
import spack.spec
import spack.stage
from spack.main import SpackCommand
from spack.util import tty
from spack.util.compression import extension_from_magic_numbers

#: How to open each tarball, by its extension as spack determines it. untar decompresses gzip
#: itself, and verifies its checksum.
OPENERS = {
    "tar": open,
    "tar.gz": open,
    "tgz": open,
    "tar.bz2": bz2.open,
    "tbz2": bz2.open,
    "tbz": bz2.open,
    "tar.xz": lzma.open,
    "txz": lzma.open,
}


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

        # Like spack, classify the archive by its magic, and by its name for old tarballs
        opener = OPENERS.get(extension_from_magic_numbers(stage.archive_file, decompress=True))
        if opener is None:
            tty.info(f"Skipped {label}: {os.path.basename(url)} is not a tarball")
            return True

        try:
            with opener(stage.archive_file, "rb") as f:
                untar.extract(f, os.path.join(stage.path, "untar"))
        except (untar.Error, OSError, EOFError, lzma.LZMAError) as e:
            tty.error(f"Invalid tarball {label}: {e}\n    {url}")
            return False

    tty.info(f"Validated {label} --> {os.path.basename(url)}")
    return True


def main():
    parser = argparse.ArgumentParser(description="strictly validate tarballs of added versions")
    parser.add_argument("from_ref", help="git ref to compare against")
    parser.add_argument("to_ref", help="git ref with the added versions")
    args = parser.parse_args()

    added = SpackCommand("repo")(
        "show-version-updates",
        "--no-manual-packages",
        "--no-git-versions",
        "builtin",
        args.from_ref,
        args.to_ref,
    )

    success = True
    for line in added.splitlines():
        spec = spack.spec.Spec(line)
        pkg = spack.repo.PATH.get_pkg_class(spec.name)(spec)
        info = pkg.versions[spec.version]
        if info.get("expand", True):
            success &= verify(pkg, spec.version, info["sha256"])

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
