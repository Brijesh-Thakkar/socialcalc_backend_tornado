#!/usr/bin/env python
#
# Aspiring Apps Web Site
# Ramu Ramamurthy
#
#

import sys
import os
import subprocess
import logging
import os.path
import re
import tornado.auth
import tornado.database
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
from tornado.options import define, options
import tornado.escape
import cloud.storage.storage
import cloud.authenticate.user
import urllib.request, urllib.parse, urllib.error
import json
import random
import string
import uuid

define("port", default=8888, help="run on the given port", type=int)

class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r"/swebapp", SubscriptionHandler)
        ]
        settings = dict(
            app_title="Aspiring Investments",
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            util_path=os.path.join(os.path.dirname(__file__), "util"),
            cloud_path=os.path.join(os.path.dirname(__file__), "cloud"),            
            xsrf_cookies=False,
            cookie_secret=os.environ.get("COOKIE_SECRET"),
            login_url="/"
        )
        tornado.web.Application.__init__(self, handlers, **settings)


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

class SubscriptionHandler(BaseHandler):
    def get(self):
        action = self.get_argument('action')
        #
        #
        # Returns the json object
        if action == "getKey":
            user = self.get_argument('id')
            if user == None:
                self.finish(dict(result="fail"))
            appname = self.get_argument('appname')               
            path = ["home",user,appname, "subscribe"]
            fileobj = cloud.storage.storage.getFile(path)
            if (fileobj == None):
                # logging.info("File not found "+fname)
                self.finish(dict(result="fail"))
                return
            fileobj.data = json.dumps(fileobj.data)
            self.finish(dict(data=fileobj.data,result="ok"))

    def post(self):
        action = self.get_argument('action')
        #
        #
        # Registration
        if action == "register":
            email = self.get_argument('email')
            password = self.get_argument('password')
            print("email is ",email)
            print("password is ",password)
            if cloud.authenticate.user.user_exists(email):
                # user already exists
                print("user exists.returning..")
                # self.finish(dict(result="exist"))
                if cloud.authenticate.user.authenticate_user(email,password):
                    print("authenticate succeeded")
                    self.set_current_user(email)
                    self.finish(dict(result="ok"))
                else:
                    print("authenticate failed")
                    self.finish(dict(result="fail"))
            else:
                print("creating new user...")
                userdir = "users"
                userdirpath = ["home",userdir]
                if cloud.storage.storage.getFile(userdir) == None:
                    print("userdir is not created")
                    cloud.storage.storage.createDir(userdirpath)
                else:
                    print("userdir exists")
                cloud.authenticate.user.create_user(email,password)
                print("user created")
                self.set_current_user(email)
                self.finish(dict(result="ok"))
            return
        #
        #
        #  Activation
        if action == "activate":
            user = self.get_argument('id')
            # print "activating for user", user
            if user == None:
                self.finish(dict(result="fail"))
                return
            content = self.get_argument('content')
            content = json.loads(content)
            appname = self.get_argument('appname')
            dirpath = ["home",user,appname]
            print("dirpath is ", dirpath)
            path = ["home",user, appname, "subscribe"]
            print("path is ", path)
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                print("no directory found, creating..")
                cloud.storage.storage.createDir(dirpath)
            # dir is now created
            fileobj = cloud.storage.storage.getFile(path)
            if fileobj == None:
                cloud.storage.storage.createFile(path,content)
                self.finish(dict(result="ok"))
            else:
                cloud.storage.storage.updateFile(path,content)
                self.finish(dict(result="ok"))
        #
        #
        # Save details
        if action == "add_details":
            user = self.get_argument('id')
            appname = self.get_argument('appname')
            if user == None:
                self.finish(dict(result="fail"))
                return
            content = self.get_argument('content')
            content = json.loads(content)
            dirpath = ["home",user,appname]
            path = ["home",user,appname, "details"]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                print("no directory found, creating..")
                cloud.storage.storage.createDir(dirpath)
            # dir is now created
            fileobj = cloud.storage.storage.getFile(path)
            if fileobj == None:
                cloud.storage.storage.createFile(path,content)
                self.finish(dict(result="ok"))
            else:
                cloud.storage.storage.updateFile(path,content)
                self.finish(dict(result="ok"))