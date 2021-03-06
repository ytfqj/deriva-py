import os
import time
import uuid
import logging
import platform
from bdbag import bdbag_api as bdb, bdbag_ro as ro, BAG_PROFILE_TAG, BDBAG_RO_PROFILE_ID
from deriva.core import ErmrestCatalog, HatracStore, format_exception, get_credential, read_config, stob, \
    __version__ as VERSION
from deriva.transfer.download.processors import findProcessor


class DerivaDownload(object):
    """

    """
    def __init__(self, server,
                 output_dir=None, kwargs=None, config=None, config_file=None, credentials=None, credential_file=None):
        self.server = server
        self.hostname = None
        self.output_dir = output_dir if output_dir else "."
        self.envars = kwargs if kwargs else dict()
        self.catalog = None
        self.store = None
        self.config = config
        self.cancelled = False
        self.credentials = credentials if credentials else dict()
        self.metadata = dict()
        self.sessions = dict()

        info = "%s v%s [Python %s, %s]" % (
            self.__class__.__name__, VERSION, platform.python_version(), platform.platform(aliased=True))
        logging.info("Initializing downloader: %s" % info)

        if not self.server:
            raise RuntimeError("Server not specified!")

        # server variable initialization
        self.hostname = self.server.get('host', '')
        if not self.hostname:
            raise RuntimeError("Host not specified!")
        protocol = self.server.get('protocol', 'https')
        self.server_url = protocol + "://" + self.hostname
        catalog_id = self.server.get("catalog_id", "1")
        session_config = self.server.get('session')

        # credential initialization
        if credential_file:
            self.credentials = get_credential(self.hostname, credential_file)

        # catalog and file store initialization
        if self.catalog:
            del self.catalog
        self.catalog = ErmrestCatalog(
            protocol, self.hostname, catalog_id, self.credentials, session_config=session_config)
        if self.store:
            del self.store
        self.store = HatracStore(
            protocol, self.hostname, self.credentials, session_config=session_config)

        # process config file
        if config_file and os.path.isfile(config_file):
            self.config = read_config(config_file)

    def setConfig(self, config):
        self.config = config

    def setCredentials(self, credentials):
        self.catalog.set_credentials(credentials, self.hostname)
        self.store.set_credentials(credentials, self.hostname)
        self.credentials = credentials

    def download(self, identity=None):

        if not self.config:
            raise RuntimeError("No configuration specified!")

        if self.config.get("catalog") is None:
            raise RuntimeError("Catalog configuration error!")

        if not identity:
            logging.info("Validating credentials")
            try:
                if not self.credentials:
                    self.setCredentials(get_credential(self.hostname))
                attributes = self.catalog.get_authn_session().json()
                identity = attributes["client"]
            except Exception as e:
                raise RuntimeError("Unable to validate credentials: %s" % format_exception(e))

        ro_manifest = None
        ro_author_name = None
        ro_author_orcid = None
        remote_file_manifest = os.path.abspath(
            ''.join([os.path.join(self.output_dir, 'remote-file-manifest_'), str(uuid.uuid4()), ".json"]))

        catalog_config = self.config['catalog']
        self.envars.update(self.config.get('env', dict()))

        bag_path = None
        bag_archiver = None
        bag_algorithms = None
        bag_config = self.config.get('bag')
        create_bag = True if bag_config else False
        if create_bag:
            bag_name = bag_config.get('bag_name', ''.join(["deriva_bag", '_', time.strftime("%Y-%m-%d_%H.%M.%S")]))
            bag_path = os.path.abspath(os.path.join(self.output_dir, bag_name))
            bag_archiver = bag_config.get('bag_archiver')
            bag_algorithms = bag_config.get('bag_algorithms', ['sha256'])
            bag_metadata = bag_config.get('bag_metadata', {"Internal-Sender-Identifier":
                                                           "deriva@%s" % self.server_url})
            bag_ro = create_bag and stob(bag_config.get('bag_ro', "True"))
            if create_bag:
                bdb.ensure_bag_path_exists(bag_path)
                bag = bdb.make_bag(bag_path, algs=bag_algorithms, metadata=bag_metadata)
                if bag_ro:
                    ro_author_name = bag.info.get("Contact-Name",
                                                  identity.get('full_name',
                                                               identity.get('display_name',
                                                                            identity.get('id', None))))
                    ro_author_orcid = bag.info.get("Contact-Orcid")
                    ro_manifest = ro.init_ro_manifest(author_name=ro_author_name, author_orcid=ro_author_orcid)
                    bag_metadata.update({BAG_PROFILE_TAG: BDBAG_RO_PROFILE_ID})

        file_list = list()
        base_path = bag_path if bag_path else self.output_dir
        for query in catalog_config['queries']:
            query_path = query['query_path']
            output_format = query['output_format']
            output_processor = query.get("output_format_processor")
            format_args = query.get('output_format_params', None)
            output_path = query.get('output_path', '')

            try:
                download_processor = findProcessor(output_format, output_processor)
                processor = download_processor(self.envars,
                                               bag=create_bag,
                                               catalog=self.catalog,
                                               store=self.store,
                                               query=query_path,
                                               base_path=base_path,
                                               sub_path=output_path,
                                               format_args=format_args,
                                               remote_file_manifest=remote_file_manifest,
                                               ro_manifest=ro_manifest,
                                               ro_author_name=ro_author_name,
                                               ro_author_orcid=ro_author_orcid)
                file_list.extend(processor.process())
            except Exception as e:
                logging.error(format_exception(e))
                if create_bag:
                    bdb.cleanup_bag(bag_path)
                raise

        if create_bag:
            try:
                if ro_manifest:
                    ro.write_bag_ro_metadata(ro_manifest, bag_path)
                if not os.path.isfile(remote_file_manifest):
                    remote_file_manifest = None
                bdb.make_bag(bag_path, algs=bag_algorithms, remote_file_manifest=remote_file_manifest, update=True)
            except Exception as e:
                logging.fatal("Exception while updating bag manifests: %s", format_exception(e))
                bdb.cleanup_bag(bag_path)
                raise
            finally:
                if remote_file_manifest and os.path.isfile(remote_file_manifest):
                    os.remove(remote_file_manifest)

            logging.info('Created bag: %s' % bag_path)

            if bag_archiver is not None:
                try:
                    archive = bdb.archive_bag(bag_path, bag_archiver.lower())
                    bdb.cleanup_bag(bag_path)
                    return [archive]
                except Exception as e:
                    logging.error("Exception while creating data bag archive:", format_exception(e))
                    raise
            else:
                return [bag_path]

        return file_list


class GenericDownloader(DerivaDownload):

    def __init__(self, server, **kwargs):
        DerivaDownload.__init__(self, server, **kwargs)
