import os
import sys
import traceback
from deriva.transfer import DerivaUpload
from deriva.core import BaseCLI

if sys.version_info > (3,):
    from urllib.parse import urlparse
else:
    from urlparse import urlparse


class DerivaUploadCLI(BaseCLI):
    def __init__(self, uploader, description, epilog):
        if not issubclass(uploader, DerivaUpload):
            raise TypeError("DerivaUpload subclass required")

        BaseCLI.__init__(self, description, epilog, uploader.getVersion())
        self.remove_options(['--host'])
        self.parser.add_argument('--no-cfg-update', action="store_true", help="Do not update local config from server.")
        self.parser.add_argument("--catalog", default=1, metavar="<1>", help="Catalog number. Default: 1")
        self.parser.add_argument("--token", default=1, metavar="<auth-token>", help="Authorization bearer token.")
        self.parser.add_argument('host', metavar='<host>', help="Fully qualified host name.")
        self.parser.add_argument("path", metavar="<dir>", help="Path to an input directory.")
        self.uploader = uploader

    @staticmethod
    def upload(uploader,
               data_path,
               hostname,
               catalog=1,
               token=None,
               config_file=None,
               credential_file=None,
               no_update=False):

        if not issubclass(uploader, DerivaUpload):
            raise TypeError("DerivaUpload subclass required")

        assert hostname
        server = dict()
        server["catalog_id"] = catalog
        if hostname.startswith("http"):
            url = urlparse(hostname)
            server["protocol"] = url.scheme
            server["host"] = url.netloc
        else:
            server["protocol"] = "https"
            server["host"] = hostname

        deriva_uploader = uploader(config_file, credential_file, server)
        if token:
            auth_token = {"cookie": "webauthn=%s" % token}
            deriva_uploader.setCredentials(auth_token)
        if not config_file and not no_update:
            deriva_uploader.getUpdatedConfig()
        deriva_uploader.scanDirectory(data_path, False)
        deriva_uploader.uploadFiles()
        deriva_uploader.cleanup()

    def main(self):
        sys.stderr.write("\n")
        args = self.parse_cli()
        if args.path is None:
            print("\nError: Input directory not specified.\n")
            self.parser.print_usage()
            return 1

        try:
            DerivaUploadCLI.upload(self.uploader,
                                   os.path.abspath(args.path),
                                   args.host,
                                   args.catalog,
                                   args.token,
                                   args.config_file,
                                   args.credential_file,
                                   args.no_cfg_update)
        except RuntimeError:
            return 1
        except:
            traceback.print_exc()
            return 1
        finally:
            sys.stderr.write("\n\n")
        return 0