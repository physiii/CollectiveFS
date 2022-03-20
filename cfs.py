#!/usr/bin/python

import argparse
import logging
import time
import json
import os
import sys
import shutil
import shlex
import subprocess
import threading
from os.path import exists
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import LoggingEventHandler
from cryptography.fernet import Fernet

ConfigDir = "/home/andy/.collective/"
ConfigFile = ConfigDir + "config"
KeyFile = ConfigDir + "key"

def sendChunk():
		print('Sending chunk to peer.')

def decryptChunk(path):
	# using the key
	fernet = Fernet(key)

	# opening the encrypted file
	with open(path, 'rb') as enc_file:
		encrypted = enc_file.read()

	# decrypting the file
	decrypted = fernet.decrypt(encrypted)

	# opening the file in write mode and
	# writing the decrypted data
	with open(path, 'wb') as dec_file:
		dec_file.write(decrypted)

def encryptChunk(path):
	# opening the original file to encrypt
	with open(path, 'rb') as file:
		original = file.read()

	# encrypting the file
	encrypted = fernet.encrypt(original)
	with open(path, 'wb') as encrypted_file:
		encrypted_file.write(encrypted)

	print('Encrypting chunk.', path)

def encryptChunks(fileFolder):
	for filename in os.scandir(fileFolder):
		if filename.is_file():
			filePath = fileFolder + "/" + filename.name
			t = threading.Thread(target=encryptChunk, args=(filePath,))
			t.start()

class ModifiedDirHandler(FileSystemEventHandler):

	def on_created(self, event):
		filePath = event.src_path
		filePathRel = event.src_path.replace(rootPath, '')
		fileName = filePath.split('/')
		fileDirPath = ""
		for dir in range(len(fileName) - 1):
			fileDirPath += fileName[dir] + '/'

		fileName = fileName[len(fileName) - 1]
		# filePathRel = event.src_path
		destPath = processPath
		if filePath.find('.collective') < 0 and not event.is_directory:
			try:
				fileFolder = processPath + filePathRel + '.d'
				makeFolder(fileFolder)
				encoderPath = programPath + '/lib/encoder'
				encoderCmd = encoderPath + " --data 128 --par 64 --out " + "\"" + fileFolder + "\" \"" + filePath + "\""
				encoderCmd = shlex.split(encoderCmd)
				encoder = subprocess.run(encoderCmd)
				subprocess.CompletedProcess(encryptChunks(fileFolder), 1)
				# for line in encoder.stdout:
				# 	if line:
				# 		print(line)
			except shutil.SameFileError:
				print('Source and destination are the same file', event, filePath, destPath)
				pass
			# print('Created: ', event, filePath, destPath)

	# def on_modified(self, event):
	# 	print('Modified: ', event);
	#
	# def on_moved(self, event):
	# 	print('Moved: ', event);
	#
	# def on_deleted(self, event):
	# 	print('Deleted: ', event);

def makeFolder(path):
	try:
			os.makedirs(path)
	except OSError as error:
			pass

def pathToDict(path):
	d = {'name': os.path.basename(path)}
	if os.path.isdir(path):
			d['type'] = "directory"
			d['children'] = [pathToDict(os.path.join(path,x)) for x in os.listdir\
(path)]
	else:
			d['type'] = "file"
	return d

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="CollectiveFS")
	# parser.add_argument("action")
	parser.add_argument("--verbose", "-v", action="count")
	parser.add_argument("--version", action="count")
	parser.add_argument('--input', dest = 'input', help = "Enter source directory to watch")
	parser.add_argument('--output', dest = 'output', help = "Enter the directory to copy to")
	parser.add_argument('--service', dest = 'output', help = "Run continuously")
	args = parser.parse_args()

	if args.verbose:
		logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

	f = open(ConfigFile, "r")
	rootPath = f.readline().rstrip('\n')
	collectivePath = rootPath + '/.collective'
	processPath = rootPath + '/.collective/proc'
	cachePath = rootPath + '/.collective/cache'
	publicPath = rootPath + '/.collective/public'
	treeFilePath = rootPath + '/.collective/tree'

	tree = json.dumps(pathToDict(rootPath), indent=2)
	treeFile = open(treeFilePath, "w")

	programPath = os.path.dirname(os.path.abspath(__file__))

	makeFolder(collectivePath)
	makeFolder(processPath)
	makeFolder(cachePath)
	makeFolder(publicPath)

	# key generation and storage
	if exists(KeyFile):
		# opening the key
		with open(KeyFile, 'rb') as filekey:
				key = filekey.read()
		print('Found key.')
	else:
		key = Fernet.generate_key()
		with open(KeyFile, 'wb') as filekey:
			filekey.write(key)
		print('Creating new key.')
	# using the generated key
	fernet = Fernet(key)

	event_handler = ModifiedDirHandler()
	observer = Observer()
	observer.schedule(LoggingEventHandler(), rootPath, recursive=True)
	observer.schedule(event_handler, rootPath, recursive=True)
	observer.start()
	try:
		while True:
				time.sleep(1)
	except KeyboardInterrupt:
			observer.stop()
	observer.join()
