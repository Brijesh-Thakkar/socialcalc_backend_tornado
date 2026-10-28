#!/usr/bin/env python
#
# Aspiring Investments
#
# 
#
#

import os
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
import uuid

import json
import cloud.storage.storage
import cloud.authenticate.user

from tornado.options import define, options
# from util.amazon_ses import AmazonSES,EmailMessage

from collections import namedtuple
import urllib.request, urllib.parse, urllib.error
import dropbox
import memcache

import time
import base64
# import sync


channels = {}

PDF_BUCKET = os.getenv("PDF_S3_BUCKET", "aspiring-pdf-files")

define("port", default=8080, help="run on the given port", type=int)


class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r"/test", TestHandler)              
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

class TestHandler(BaseHandler):

    def exists_in_storage(self,fname):
        return cloud.storage.storage.existsItem(fname, PDF_BUCKET)
    def get_from_storage(self,fname):
        return cloud.storage.storage.getItem(fname, PDF_BUCKET)
    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,size))
    def get(self):
        logging.info("in htmltopdf converter get")
        fname = self.get_argument('fname')
        fullfname = "/home/ubuntu/tmp/htmltopdf/%s"%fname
        inpfile = fullfname+".pdf"
        logging.info("fname=%s"%inpfile)        
        self.set_header("Content-Type","application/pdf")
        if os.path.exists(inpfile):
            logging.info("found %s on disk"%fname)
            self.write(open(inpfile).read())
        else:
            data = self.get_from_storage(fname)
            if data:
                logging.info("found %s in s3"%fname)
                self.write(data)
            else:
                self.write("Not Found")
        
    def post(self):
        data = tornado.escape.json_decode(self.request.body)
	
        logging.info("in htmltopdf converter post")
        # run with wkhtmltopdf
        # logging.info("type is PDF")

        while True:
            fname=self.get_random_string(20)
            fullfname = "/home/ubuntu/tmp/htmltopdf/%s"%fname
            if os.path.exists(fullfname):
                continue
            elif self.exists_in_storage(fname):
                continue
            else:
                break;
        inpfile = fullfname+".html"
        s = data['content']
        print(s)
        f = open(inpfile,"w")
        f.write(s)
        f.close()
        outfile = fullfname+".pdf"
        logging.info(outfile)
        logging.info(inpfile)
        cmdname = "/usr/local/bin/wkhtmltopdf.sh"
        output = subprocess.getoutput("%s %s %s"%(cmdname, inpfile, outfile))
        pdfurl="http://"+self.request.host+"/htmltopdf?fname=%s"%fname
        self.finish(dict(pdfurl=pdfurl,result="ok"))     

print("Testing cloudmain.py")


def main():
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.instance().start()




if __name__ == "__main__":
    main()
