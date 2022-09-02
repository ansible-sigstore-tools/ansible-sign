import argparse
import getpass
import logging
import os
import sys

from ansible_sign import __version__
from ansible_sign.checksum import (
    ChecksumFile,
    ChecksumMismatch,
    InvalidChecksumLine,
)
from ansible_sign.checksum.differ import DistlibManifestChecksumFileExistenceDiffer
from ansible_sign.signing import GPGSigner, GPGVerifier

__author__ = "Rick Elrod"
__copyright__ = "(c) 2022 Red Hat, Inc."
__license__ = "MIT"

_logger = logging.getLogger(__name__)

# This is relative to the project root passed in by the user at runtime.
ANSIBLE_SIGN_DIR = ".ansible-sign"


def parse_args(args):
    """Parse command line parameters

    Args:
      args (List[str]): command line parameters as list of strings
          (for example  ``["--help"]``).

    Returns:
      :obj:`argparse.Namespace`: command line parameters namespace
    """

    parser = argparse.ArgumentParser(description="Signing and validation for Ansible content")
    parser.add_argument(
        "--version",
        action="version",
        version="ansible-sign {ver}".format(ver=__version__),
    )
    parser.add_argument(
        "--debug",
        help="Print a bunch of debug info",
        action="store_const",
        dest="loglevel",
        const=logging.DEBUG,
    )
    parser.add_argument(
        "--nocolor",
        help="Disable color output",
        required=False,
        dest="nocolor",
        default=True if len(os.environ.get("NO_COLOR", "")) else False,
        action="store_true",
    )

    # Future-proofing for future content types.
    content_type_parser = parser.add_subparsers(required=True, dest="content_type")

    project = content_type_parser.add_parser(
        "project",
        help="Act on an Ansible project directory",
    )
    project_commands = project.add_subparsers(required=True, dest="command")

    # command: gpg-verify
    cmd_gpg_verify = project_commands.add_parser(
        "gpg-verify",
        help=("Perform signature validation AND checksum verification on the checksum manifest"),
    )
    cmd_gpg_verify.set_defaults(func=gpg_verify)
    cmd_gpg_verify.add_argument(
        "--keyring",
        help=("The GPG keyring file to use to find the matching public key. (default: the user's default keyring)"),
        required=False,
        metavar="KEYRING",
        dest="keyring",
        default=None,
    )
    cmd_gpg_verify.add_argument(
        "--gnupg-home",
        help=("A valid GnuPG home directory. (default: the GnuPG default, usually ~/.gnupg)"),
        required=False,
        metavar="GNUPG_HOME",
        dest="gnupg_home",
        default=None,
    )
    cmd_gpg_verify.add_argument(
        "project_root",
        help="The directory containing the files being validated and verified",
        metavar="PROJECT_ROOT",
    )

    # command: gpg-sign
    cmd_gpg_sign = project_commands.add_parser(
        "gpg-sign",
        help="Generate a checksum manifest and GPG sign it",
    )
    cmd_gpg_sign.set_defaults(func=gpg_sign)
    cmd_gpg_sign.add_argument(
        "--fingerprint",
        help=("The GPG private key fingerprint to sign with. (default: First usable key in the user's keyring)"),
        required=False,
        metavar="PRIVATE_KEY",
        dest="fingerprint",
        default=None,
    )
    cmd_gpg_sign.add_argument(
        "-p",
        "--prompt-passphrase",
        help="Prompt for a GPG key passphrase",
        required=False,
        dest="prompt_passphrase",
        default=False,
        action="store_true",
    )
    cmd_gpg_sign.add_argument(
        "--gnupg-home",
        help=("A valid GnuPG home directory. (default: the GnuPG default, usually ~/.gnupg)"),
        required=False,
        metavar="GNUPG_HOME",
        dest="gnupg_home",
        default=None,
    )
    cmd_gpg_sign.add_argument(
        "project_root",
        help="The directory containing the files being validated and verified",
        metavar="PROJECT_ROOT",
    )
    return parser.parse_args(args)


def setup_logging(loglevel):
    """Setup basic logging

    Args:
      loglevel (int): minimum loglevel for emitting messages
    """
    logformat = "[%(asctime)s] %(levelname)s:%(name)s:%(message)s"
    logging.basicConfig(level=loglevel, stream=sys.stdout, format=logformat, datefmt="%Y-%m-%d %H:%M:%S")


def _generate_checksum_manifest(project_root):
    differ = DistlibManifestChecksumFileExistenceDiffer
    checksum = ChecksumFile(project_root, differ=differ)
    try:
        manifest = checksum.generate_gnu_style()
    except FileNotFoundError as e:
        if str(e).endswith("/MANIFEST.in"):
            print("Could not find a MANIFEST.in file in the specified project.")
            print("If you are attempting to sign a project, please create this file.")
            print("See the ansible-sign documentation for more information.")
            return False
        raise e
    _logger.debug(
        "Full calculated checksum manifest (%s):\n%s",
        project_root,
        manifest,
    )
    return manifest


def _error(nocolor, msg):
    if nocolor:
        print(f"[ERROR] {msg}")
    else:
        print(f"[\033[91mERROR\033[0m] {msg}")


def _ok(nocolor, msg):
    if nocolor:
        print(f"[OK   ] {msg}")
    else:
        print(f"[\033[92mOK   \033[0m] {msg}")


def _note(nocolor, msg):
    if nocolor:
        print(f"[NOTE ] {msg}")
    else:
        print(f"[\033[94mNOTE \033[0m] {msg}")


def validate_checksum(args):
    """
    Validate a checksum manifest file. Print a pretty message and return an
    appropriate exit code.

    NOTE that this function does not actually check the path for existence, it
    leaves that to the caller (which in nearly all cases would need to do so
    anyway). This function will throw FileNotFoundError if the manifest does not
    exist.
    """
    differ = DistlibManifestChecksumFileExistenceDiffer
    checksum = ChecksumFile(args.project_root, differ=differ)
    checksum_path = os.path.join(args.project_root, ".ansible-sign", "sha256sum.txt")

    checksum_file_contents = open(checksum_path, "r").read()

    try:
        manifest = checksum.parse(checksum_file_contents)
    except InvalidChecksumLine as e:
        _error(args.nocolor, f"Invalid line encountered in checksum manifest: {e}")
        return 1

    try:
        checksum.verify(manifest, diff=True)
    except ChecksumMismatch as e:
        _error(args.nocolor, "Checksum validation failed.")
        _error(args.nocolor, str(e))
        return 2
    except FileNotFoundError as e:
        if str(e).endswith("/MANIFEST.in"):
            _error(args.nocolor, "Could not find a MANIFEST.in file in the specified project.")
            _note(args.nocolor, "If you are attempting to verify a signed project, please ensure that the project directory includes this file after signing.")
            _note(args.nocolor, "See the ansible-sign documentation for more information.")
            return 1

    _ok(args.nocolor, "Checksum validation succeeded.")
    return 0


def gpg_verify(args):
    signature_file = os.path.join(args.project_root, ".ansible-sign", "sha256sum.txt.sig")
    manifest_file = os.path.join(args.project_root, ".ansible-sign", "sha256sum.txt")

    if not os.path.exists(signature_file):
        _error(args.nocolor, f"Signature file does not exist: {signature_file}")
        return 1

    if not os.path.exists(manifest_file):
        _error(args.nocolor, f"Checksum manifest file does not exist: {manifest_file}")
        return 1

    if args.keyring is not None and not os.path.exists(args.keyring):
        _error(args.nocolor, f"Specified keyring file not found: {args.keyring}")
        return 1

    if args.gnupg_home is not None and not os.path.isdir(args.gnupg_home):
        _error(args.nocolor, f"Specified GnuPG home is not a directory: {args.gnupg_home}")
        return 1

    verifier = GPGVerifier(
        manifest_path=manifest_file,
        detached_signature_path=signature_file,
        gpg_home=args.gnupg_home,
        keyring=args.keyring,
    )

    result = verifier.verify()

    if result.success is not True:
        _error(args.nocolor, result.summary)
        _note(args.nocolor, "Re-run with the global --debug flag for more information.")
        _logger.debug(result.extra_information)
        return 3

    _ok(args.nocolor, result.summary)

    # GPG verification is done and we are still here, so return based on
    # checksum validation now.
    return validate_checksum(args)


def _write_file_or_print(dest, contents):
    if dest == "-":
        print(contents, end="")
        return

    outdir = os.path.dirname(dest)

    if len(outdir) > 0 and not os.path.isdir(outdir):
        _logger.info("Creating output directory: %s", outdir)
        os.makedirs(outdir)

    with open(dest, "w") as f:
        f.write(contents)
        _logger.info("Wrote to file: %s", dest)


def gpg_sign(args):
    # Step 1: Manifest
    manifest_path = os.path.join(args.project_root, ".ansible-sign", "sha256sum.txt")
    checksum_file_contents = _generate_checksum_manifest(args.project_root)
    if checksum_file_contents is False:
        return 1
    _write_file_or_print(manifest_path, checksum_file_contents)

    # Step 2: Signing
    # Do they need a passphrase?
    passphrase = None
    if args.prompt_passphrase:
        passphrase = getpass.getpass("GPG Key Passphrase: ")

    signature_path = os.path.join(args.project_root, ".ansible-sign", "sha256sum.txt.sig")
    signer = GPGSigner(
        manifest_path=manifest_path,
        output_path=signature_path,
        privkey=args.fingerprint,
        passphrase=passphrase,
        gpg_home=args.gnupg_home,
    )
    result = signer.sign()
    if result.success:
        _ok(args.nocolor, "GPG signing successful!")
        retcode = 0
    else:
        _error(args.nocolor, "GPG signing FAILED!")
        _note(args.nocolor, "Re-run with the global --debug flag for more information.")
        retcode = 4

    _note(args.nocolor, f"Checksum manifest: {manifest_path}")
    _note(args.nocolor, f"GPG summary: {result.summary}")
    _logger.debug(f"GPG Details: {result.extra_information}")
    return retcode


def main(args):
    args = parse_args(args)
    setup_logging(args.loglevel)
    _logger.debug("Starting crazy calculations...")
    exitcode = args.func(args)
    _logger.info("Script ends here")
    return exitcode


def run():
    """Calls :func:`main` passing the CLI arguments extracted from :obj:`sys.argv`

    This function can be used as entry point to create console scripts with setuptools.
    """
    return main(sys.argv[1:])


if __name__ == "__main__":
    run()
