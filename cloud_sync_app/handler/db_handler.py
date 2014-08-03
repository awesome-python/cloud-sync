# -*- coding:utf-8 -*-

import os, sys
import sqlite3
from ..fsmonitor.fsmonitor import *
from ..helper.sync_helper import SyncHelper

class DBHandler(object):

    def __init__(self, settings, logger):
        self.settings = settings
        self.logger = logger


    def setup_db(self):
        self.db_queue = Queue.Queue()
        # Create connection to synced files DB.
        self.dbcon = sqlite3.connect(self.settings.SYNCED_FILES_DB)
        self.dbcon.text_factory = unicode # This is the default, but we set it explicitly, just to be sure.
        self.dbcur = self.dbcon.cursor()
        self.dbcur.execute("CREATE TABLE IF NOT EXISTS synced_files(input_file text, transported_file_basename text, url text, server text)")
        self.dbcur.execute("CREATE UNIQUE INDEX IF NOT EXISTS file_unique_per_server ON synced_files (input_file, server)")
        self.dbcon.commit()
        self.dbcur.execute("SELECT COUNT(input_file) FROM synced_files")
        num_synced_files = self.dbcur.fetchone()[0]
        self.logger.warning("Setup: connected to the synced files DB. Contains metadata for %d previously synced files." % (num_synced_files))

    def shutdown(self):
        # Log information about the synced files DB.
        self.dbcur.execute("SELECT COUNT(input_file) FROM synced_files")
        num_synced_files = self.dbcur.fetchone()[0]
        self.logger.warning("Synced files DB contains metadata for [%d] synced files." % (num_synced_files))

    def process_db_queue(self):
        processed = 0 
        # TODO: Alter configuration.
        syncHelper = SyncHelper(
                cogenda_shared_secret=self.setting.COGENDA_SHARED_SECRET,
                ws_host=WS_HOST,
                api_modify_resource=API_MODIFY_RESOURCE,
                api_destroy_resource=API_DESTROY_RESOURCE)

        while processed < QUEUE_PROCESS_BATCH_SIZE and self.db_queue.qsize() > 0:
            # DB queue -> database.
            self.lock.acquire()
            (input_file, event, processed_for_server, output_file, transported_file, url, server) = self.db_queue.get()
            self.lock.release()
            # Commit the result to the database.            
            transported_file_basename = os.path.basename(output_file)
            if event == FSMonitor.CREATED:
                try:
                    self.dbcur.execute("INSERT INTO synced_files VALUES(?, ?, ?, ?)", (input_file, transported_file_basename, url, server))
                    self.dbcon.commit()
                except sqlite3.IntegrityError, e:
                    self.logger.critical("Database integrity error: %s. Duplicate key: input_file = '%s', server = '%s'." % (e, input_file, server))

            elif event == FSMonitor.MODIFIED:
                self.dbcur.execute("SELECT COUNT(*) FROM synced_files WHERE input_file=? AND server=?", (input_file, server))
                if self.dbcur.fetchone()[0] > 0:
                    # Look up the transported file's base name. This
                    # might be different from the input file's base
                    # name due to processing.
                    self.dbcur.execute("SELECT transported_file_basename FROM synced_files WHERE input_file=? AND server=?", (input_file, server))
                    old_transport_file_basename = self.dbcur.fetchone()[0]
                    # Update the transported_file_basename and url fields for
                    # the input_file that has been transported.
                    self.dbcur.execute("UPDATE synced_files SET transported_file_basename=?, url=? WHERE input_file=? AND server=?", (transported_file_basename, url, input_file, server))
                    self.dbcon.commit()
                else:
                    self.dbcur.execute("INSERT INTO synced_files VALUES(?, ?, ?, ?)", (input_file, transported_file_basename, url, server))
                    self.dbcon.commit()
            elif event == FSMonitor.DELETED:
                self.dbcur.execute("DELETE FROM synced_files WHERE input_file=? AND server=?", (input_file, server))
                self.dbcon.commit()
            else:
                raise Exception("Non-existing event set.")
            self.logger.debug("DB queue -> 'synced files' DB: '%s' (URL: '%s')." % (input_file, url))

            self._sync_congenda(syncHelper, event, transported_file_basename, transported_file, url, server)
        processed += 1

    def _sync_congenda(self, syncHelper, event, transported_file_basename, transported_file, url, server):
        # Sync with cogenda web server
        if OSS_DEFAULT_ACL != 'private' or AWS_DEFAULT_ACL != 'private':
            return
        if event == FSMonitor.CREATED or event == FSMonitor.MODIFIED:
            result = syncHelper.sync_resource(transported_file_basename, url, server, transported_file)
            if not result:
                self.logger.critical('Failed to sync with cogenda server filename: [%s]  vendor: [%s]' %(transported_file_basename, server))
            else:
                self.logger.info('Success to sync with cogenda server filename: [%s]  vendor: [%s]' %(transported_file_basename, server))
        elif event == FSMonitor.DELETED:
            result = syncHelper.destroy_resource(transported_file_basename, server)
            if not result:
                self.logger.critical('Failed to destory resource with cogenda server filename: [%s] vendor: [%s]' %(transported_file_basename, server))
            else:
                self.logger.info('Success to destroy resource with cogenda server filename: [%s]  vendor: [%s]' %(transported_file_basename, server))
        else:
            raise Exception("Non-existing event set.")
        self.logger.debug("Sync cogenda -> 'synced file with cogenda web server' file: '%s' (URL: '%s')." % (transported_file, url))
