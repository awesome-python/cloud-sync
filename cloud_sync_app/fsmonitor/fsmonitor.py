# -*- coding:utf-8 -*-

"""
File system monitor
"""
import sqlite3
import threading
import Queue
import os
import logging
from ..scanner.path_scanner import PathScanner


# Define exceptions.
class FSMonitorError(Exception):
    pass


class FSMonitor(threading.Thread):
    """cross-platform file system monitor"""

    # Identifiers for each event.
    EVENTS = {
        "CREATED": 0x00000001,
        "MODIFIED": 0x00000002,
        "DELETED": 0x00000004,
        "MONITORED_DIR_MOVED": 0x00000008,
        "DROPPED_EVENTS": 0x00000016,
    }

    # Will be filled at the end of this .py file.
    EVENTNAMES = {}
    MERGE_EVENTS = {}

    def __init__(self, callback, persistent=False, trigger_events_for_initial_scan=False, ignored_dirs=[], dbfile="fsmonitor.db", parent_logger=None):
        self.persistent = persistent
        self.trigger_events_for_initial_scan = trigger_events_for_initial_scan
        self.monitored_paths = {}
        self.dbfile = dbfile
        self.dbcon = None
        self.dbcur = None
        self.pathscanner = None
        self.ignored_dirs = ignored_dirs
        self.callback = callback
        self.lock = threading.Lock()
        self.add_queue = Queue.Queue()
        self.remove_queue = Queue.Queue()
        self.die = False
        if parent_logger is None:
            parent_logger = ""
        self.logger = logging.getLogger(".".join([parent_logger, "FSMonitor"]))
        threading.Thread.__init__(self, name="FSMonitorThread")

    def run(self):
        """start the file system monitor (starts a separate thread)"""
        raise NotImplemented

    def add_dir(self, path, event_mask):
        """add a directory to monitor"""
        self.lock.acquire()
        self.add_queue.put((path, event_mask))
        self.lock.release()

    def __add_dir(self, path, event_mask):
        raise NotImplemented

    def remove_dir(self, path):
        """stop monitoring a directory"""
        self.lock.acquire()
        self.remove_queue.put(path)
        self.lock.release()
        self.logger.info("Queued '%s' to stop being watched.")

    def __remove_dir(self, path):
        raise NotImplemented

    def generate_missed_events(self, path, event_mask=None):
        """generate the missed events for a persistent DB"""
        self.logger.info("Generating missed events for '%s' (event mask: %s)." % (path, event_mask))
        for event_path, result in self.pathscanner.scan_tree(path):
            self.trigger_events_for_pathscanner_result(path, event_path, result, "generate_missed_events", event_mask)
        self.logger.info("Done generating missed events for '%s' (event mask: %s)." % (path, event_mask))

    def stop(self):
        """stop the file system monitor (stops the separate thread)"""
        raise NotImplemented

    def purge_dir(self, path):
        """purge the metadata for a monitored directory
        Only possible if this is a persistent DB.
        """
        if self.persistent:
            self.pathscanner.purge_path(path)
            self.logger.info("Purged information for monitored path '%s'." % (path))

    def trigger_event(self, monitored_path, event_path, event, discovered_through):
        """trigger one of the standardized events"""
        if callable(self.callback):
            self.logger.info("Detected '%s' event for '%s' through %s (for monitored path '%s')." % (FSMonitor.EVENTNAMES[event], event_path, discovered_through, monitored_path))
            self.callback(monitored_path, event_path, event, discovered_through)

    def setup(self):
        """set up the database and pathscanner"""
        # Database.
        if self.dbcur is None:
            self.dbcon = sqlite3.connect(self.dbfile)
            # This is the default, but we set it explicitly, just to be sure.
            self.dbcon.text_factory = unicode
            self.dbcur = self.dbcon.cursor()
        # PathScanner.
        if self.persistent == True and self.dbcur is not None:
            self.pathscanner = PathScanner(self.dbcon, self.ignored_dirs, "pathscanner")

    def trigger_events_for_pathscanner_result(self, monitored_path, event_path, result, discovered_through=None, event_mask=None):
        """trigger events for pathscanner result"""
        if event_mask is None:
            event_mask = self.monitored_paths[monitored_path].event_mask
        if event_mask & FSMonitor.CREATED:
            for filename in result["created"]:
                self.trigger_event(monitored_path, os.path.join(event_path, filename), self.CREATED, discovered_through)
        if event_mask & FSMonitor.MODIFIED:
            for filename in result["modified"]:
                self.trigger_event(monitored_path, os.path.join(event_path, filename), self.MODIFIED, discovered_through)
        if event_mask & FSMonitor.DELETED:
            for filename in result["deleted"]:
                self.trigger_event(monitored_path, os.path.join(event_path, filename), self.DELETED, discovered_through)

    def is_in_ignored_directory(self, path):
        """checks if the given path is in an ignored directory"""
        dirs = os.path.split(path)
        for dir in dirs:
            if dir in self.ignored_dirs:
                return True
        return False

class MonitoredPath(object):
    """A simple container for all metadata related to a monitored path"""
    def __init__(self, path, event_mask, fsmonitor_ref=None):
        self.path = path
        self.event_mask = event_mask
        self.fsmonitor_ref = fsmonitor_ref
        self.monitoring = False

def __get_class_reference(modulename, classname):
    """get a reference to a class"""
    module = __import__(modulename, globals(), locals(), [classname])
    class_reference = getattr(module, classname)
    return class_reference

def get_fsmonitor():
    """get the FSMonitor for the current platform"""
    # Default to a polling mechanism
    return __get_class_reference("fsmonitor_polling", "FSMonitorPolling")

# Make EVENTS' members directly accessible through the class dictionary. Also
# fill the FSMonitor.EVENTNAMES dictionary.
for name, mask in FSMonitor.EVENTS.iteritems():
    setattr(FSMonitor, name, mask)
    FSMonitor.EVENTNAMES[mask] = name

# Fill the FSMonitor.MERGE_EVENTS nested dictionary.
# Key at level 1: old event. Key at level 2: new event. Value: merged event.
# A value (merged event) of None means that the events have canceled each
# other out, i.e. that nothing needs to happen (this is only the case when a
# file is deleted immediately after it has been created).
# Some of these combinations (marked with a #!) should not logically happen,
# but all possible cases are listed anyway, for maximum robustness. They may
# still happen due to bugs in the operating system's API, for example.
FSMonitor.MERGE_EVENTS[FSMonitor.CREATED] = {}
FSMonitor.MERGE_EVENTS[FSMonitor.CREATED][FSMonitor.CREATED] = FSMonitor.CREATED
FSMonitor.MERGE_EVENTS[FSMonitor.CREATED][FSMonitor.MODIFIED] = FSMonitor.CREATED
FSMonitor.MERGE_EVENTS[FSMonitor.CREATED][FSMonitor.DELETED] = None
FSMonitor.MERGE_EVENTS[FSMonitor.MODIFIED] = {}
FSMonitor.MERGE_EVENTS[FSMonitor.MODIFIED][FSMonitor.CREATED] = FSMonitor.MODIFIED
FSMonitor.MERGE_EVENTS[FSMonitor.MODIFIED][FSMonitor.MODIFIED] = FSMonitor.MODIFIED
FSMonitor.MERGE_EVENTS[FSMonitor.MODIFIED][FSMonitor.DELETED] = FSMonitor.DELETED
FSMonitor.MERGE_EVENTS[FSMonitor.DELETED] = {}
FSMonitor.MERGE_EVENTS[FSMonitor.DELETED][FSMonitor.CREATED] = FSMonitor.MODIFIED
FSMonitor.MERGE_EVENTS[FSMonitor.DELETED][FSMonitor.MODIFIED] = FSMonitor.MODIFIED
FSMonitor.MERGE_EVENTS[FSMonitor.DELETED][FSMonitor.DELETED] = FSMonitor.DELETED
