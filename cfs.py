import argparse
import logging
import time

import os
import sys
import shutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import LoggingEventHandler


class ModifiedDirHandler(FileSystemEventHandler):
	def on_created(self, event):
		filePath = event.src_path
		destPath = os.path.abspath(args.output)
		try:
			shutil.copy(filePath, destPath)
			print('Copied: ', event, filePath, destPath)
		except shutil.SameFileError:
			print('Source and destination are the same file', event, filePath, destPath)
			pass
		print('Created: ', event, filePath, destPath)

	def on_modified(self, event):
		print('Modified: ', event);

	def on_moved(self, event):
		print('Moved: ', event);

	def on_deleted(self, event):
		print('Deleted: ', event);

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="CollectiveFS")
	# parser.add_argument("action")
	parser.add_argument("--verbose", "-v", action="count")
	parser.add_argument("--version", action="count")
	parser.add_argument('--input', dest = 'input', help = "Enter source directory to watch")
	parser.add_argument('--output', dest = 'output', help = "Enter the directory to copy to")
	args = parser.parse_args()

	if args.verbose:
		logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

	path = os.path.abspath(args.input)
	event_handler = ModifiedDirHandler()
	observer = Observer()
	observer.schedule(LoggingEventHandler(), path, recursive=True)
	observer.schedule(event_handler, path, recursive=True)
	observer.start()
	try:
		while True:
				# print('Waiting for file system changes.');
				time.sleep(1)
	except KeyboardInterrupt:
			observer.stop()
	observer.join()
