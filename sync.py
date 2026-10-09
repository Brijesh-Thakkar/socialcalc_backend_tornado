#!/usr/bin/env python3
#
# Aspiring Investments
#
# 
#
import subprocess
import logging
import os.path
import re
import tornado.auth
#import tornado.database
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
import unicodedata

import random
import string
import json
import cloud.storage.storage
import cloud.authenticate.user

class BaseHandler(tornado.web.RequestHandler):
    @property
    def db(self):
        return self.application.db

    def get_current_user(self):
        user_json = self.get_secure_cookie("user")
        if user_json:
            return tornado.escape.json_decode(user_json)
        else:
            return None

    def set_current_user(self, user):
        if user:
            self.set_secure_cookie("user", tornado.escape.json_encode(user))
        else:
            self.clear_cookie("user")

class SyncHandler(BaseHandler):
    def get(self):
        user = self.get_current_user()
        if user == None:
            self.finish(dict(result="fail"))
            return
        appname = self.get_argument('appname')
        action = self.get_argument('action')
        if action == "get-entry":
            dirpath = ["home",user,"securestore","sync",appname]
            dirobj = cloud.storage.storage.getFile(dirpath)
            path = ["home",user,"securestore","sync",appname,"entries"]
            fileobj = cloud.storage.storage.getFile(path)
            if (fileobj == None):
                logging.info("Error: 56")
                self.finish(dict(result="fail"))
                return
            self.finish(dict(data=fileobj.data,result="ok"))


    def post(self):
        user = self.get_current_user()
        if user == None:
            self.finish(dict(result="fail"))
            return
        args = self.request.arguments
        appname = self.get_argument('appname')
        action = self.get_argument('action')
        if action == "add-entry":
            entry = json.loads(self.get_argument('entry'))
            dirpath = ["home",user,"securestore","sync",appname]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                # logging.info("no directory restore")
                cloud.storage.storage.createDir(dirpath)
            path = ["home",user,"securestore","sync",appname,"entries"]
            fileobj = cloud.storage.storage.getFile(path)
            cloud.storage.storage.createFile(path, entry)
            self.finish(dict(result="ok"))