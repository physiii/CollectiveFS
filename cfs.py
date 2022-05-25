#!/usr/bin/python

import uuid
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
import filexfer.filexfer as transfer

from os.path import exists
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog.events import LoggingEventHandler
from cryptography.fernet import Fernet

from http.server import BaseHTTPRequestHandler, HTTPServer

import simplejson
import random

ConfigDir = "/home/andy/.collective/"
ConfigFile = ConfigDir + "config"
KeyFile = ConfigDir + "key"

HOST_NAME = 'localhost'
PORT_NUMBER = 8080

class ServerReqHandler(BaseHTTPRequestHandler):
    def _set_headers(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def do_GET(self):
        self._set_headers()
        f = open("index.html", "rb")
        self.wfile.write(f.read())

    def do_HEAD(self):
        self._set_headers()

    def do_POST(self):
        self._set_headers()
        print("in post method")
        self.data_string = self.rfile.read(int(self.headers['Content-Length']))

        self.send_response(200)
        self.end_headers()

        data = simplejson.loads(self.data_string)
        with open("test123456.json", "w") as outfile:
            simplejson.dump(data, outfile)
        print("{}".format(data))
        f = open("for_presen.py")
        self.wfile.write(f.read())
        return

def run_server():
	httpd = HTTPServer((HOST_NAME, PORT_NUMBER), ServerReqHandler)
	t = threading.Thread(target=httpd.serve_forever)
	t.start()

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

def encryptChunk(fileInfo, chunkInfo):
	# opening the original file to encrypt
	with open(chunkInfo['path'], 'rb') as file:
		original = file.read()

	# encrypting the file
	encrypted = fernet.encrypt(original)
	with open(chunkInfo['path'], 'wb') as encrypted_file:
		encrypted_file.write(encrypted)

	# t = threading.Thread(target=transfer.start_transfer, args=("send", fileInfo, chunkInfo))
	# t.start()
	transfer.start_transfer("send", fileInfo, chunkInfo)

	print('Encrypted chunk.', filePath)

def encryptChunks(fileInfo):
	fileFolder = fileInfo['folder']
	for filename in os.scandir(fileFolder):
		if filename.is_file():
			chunkNum = int(os.path.splitext(filename.name)[1][1:])
			filePath = fileFolder + "/" + filename.name
			chunkInfo = {
				"num": chunkNum,
				"id": str(uuid.uuid4()),
				"path": filePath,
				"offer": {},
				"answer": {}
			}
			fileInfo['chunks'].insert(chunkNum, chunkInfo)
			t = threading.Thread(target=encryptChunk, args=(fileInfo, chunkInfo))
			t.start()
	print(json.dumps(fileInfo))

def finishedEncoding(fileInfo):
	encryptChunks(fileInfo)

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
		if filePath.find('.collective') < 0 and not event.is_directory:
			try:
				numOfDataChunks = 64 #128
				numOfParChunks =  32 #64
				id = uuid.uuid4();
				fileFolder = processPath + filePathRel + '.d'
				# fileFolder = processPath + '/' + str(id)
				makeFolder(fileFolder)
				encoderPath = programPath + '/lib/encoder'
				encoderCmd = encoderPath + " --data " + str(numOfDataChunks) + " --par " + str(numOfParChunks) + " --out " + "\"" + fileFolder + "\" \"" + filePath + "\""
				encoderCmd = shlex.split(encoderCmd)
				encoder = subprocess.run(encoderCmd)

				num = numOfParChunks + numOfDataChunks
				fileInfo = {
					"id": str(id),
					"name": fileName,
					"folder": fileFolder,
					"number_of_chunks": num,
					"chunks": []
				}

				subprocess.CompletedProcess(finishedEncoding(fileInfo), 1)
				# for line in encoder.stdout:
				# 	if line:
				# 		print(line)
			except shutil.SameFileError:
				print('Source and destination are the same file', event, filePath, processPath)
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
	# treeFile = open(treeFilePath, "w")

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

	run_server()

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
