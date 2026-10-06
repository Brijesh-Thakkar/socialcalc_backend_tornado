#!/usr/bin/env python3
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
from util.amazon_ses import AmazonSES,EmailMessage

from collections import namedtuple
import urllib.request, urllib.parse, urllib.error
import memcache

import time
import base64
import sync

import asyncio

#import util.ystockquote
#import util.simpledb
#import util.tickersymbols

channels = {}

define("port", default=8080, help="run on the given port", type=int)
#define("mysql_host", default="127.0.0.1:3306", help="database host")
#define("mysql_database", default="aspiringinvestments", help="database name")
#define("mysql_user", default="ai", help="database user")
#define("mysql_password", default="ai", help="database password")
  
HTMLTOPDF_BASE = os.environ.get(
    "HTMLTOPDF_BASE",
    "/home/ubuntu/tmp"
)
PDF_BUCKET = os.getenv(
    "PDF_S3_BUCKET",
    "aspiring-pdf-files"
)
IMG_BUCKET = os.getenv("IMG_S3_BUCKET", "aspiring-img-files")
# Public base URL used to build pdfurl in responses.
# Trailing slashes are stripped so the path join is always clean.
# Falls back to reconstructing the URL from the incoming request when unset.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r"/dev", HomeHandler),
            (r"/save", SaveHandler),
            (r"/search", SearchHandler),
            (r"/runas", RunAsHandler),
            (r"/runasemailer",RunAsEmailHandler),            
            (r"/usersheet", UserSheetHandler),
            (r"/insert", InsertHandler),
            (r"/import", ImportHandler),            
            (r"/downloadfile",DownloadFileHandler),
            (r"/htmltopdf",HtmlToPdfHandler),
            (r"/iconimg",IconImgHandler),
            (r"/login",UserLoginHandler),
            (r"/logout",UserLogoutHandler),            
            (r"/register",UserRegisterHandler),
            (r"/lostpw",UserLostPasswordHandler),
            (r"/webapp",WebAppHandler),
            (r"/meshkit", MeshkitHandler),
            (r"/meshkit/upload", MeshkitSidecarHandler),
            (r"/meshkit/retrieve/(.*)", MeshkitSidecarHandler),
            (r"/meshkit/list", MeshkitSidecarHandler),
            (r"/meshkit-kubo/upload", MeshkitKuboSidecarHandler),
            (r"/meshkit-kubo/retrieve/(.*)", MeshkitKuboSidecarHandler),
            (r"/meshkit-kubo/list", MeshkitKuboSidecarHandler),
            (r"/pwreset",PwResetHandler),
            (r"/dropbox", DropBoxHandler),
            (r"/inapp", InAppHandler),
            (r"/restore", RestoreInAppHandler),
            (r"/amazonwebapp/(?P<param1>[^\/]+)/randomCode/(?P<param2>[^\/]+)", AmazonWebAppHandler),
            (r"/finrecord", FinanceRecordKeeper),
            (r"/bisrecord", BusinessRecordKeeper),
            (r"/sync", sync.SyncHandler),


            #(r"/multisheet", MultiSheetHandler),
            #(r"/templates",TemplatesHandler),
            #(r"/stock", StockHandler),
            (r"/broadcast", MessageNewHandler),
            (r"/updates", MessageUpdateHandler),
            #(r"/sharedsession", SharedSessionHandler),
            #(r"/uploadtest", UploadTestHandler),

            #(r"/ticker", TickerHandler),
            #(r"/tenyeardata", TenYearDataHandler),
            #(r"/embed(.*)", EmbedHandler),            
            (r"/collaborate(.*)", CollaborateHandler),            
            #(r"/share", ShareHandler),
            #(r"/tickerjson", TickerJsonHandler)              
        ]
        settings = dict(
            app_title="Aspiring Investments",
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            util_path=os.path.join(os.path.dirname(__file__), "util"),
            cloud_path=os.path.join(os.path.dirname(__file__), "cloud"),            
            xsrf_cookies=False,
            cookie_secret=os.environ.get('COOKIE_SECRET', 'fallback-local-dev-secret'),
            login_url="/"            
        )
        tornado.web.Application.__init__(self, handlers, **settings)
        
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        try:
            with open("credentials", "r") as f:
                data = f.read().split("\n")
                if len(data) >= 2:
                    aws_access_key = data[0].strip()
                    aws_secret_key = data[1].strip()
                else:
                    logging.warning("Credentials file is malformed. Falling back to environment variables.")
        except Exception as e:
            logging.warning("Credentials file could not be read (%s). Falling back to environment variables." % str(e))
            
        if aws_access_key and aws_secret_key:
            self.amazonSes = AmazonSES(aws_access_key, aws_secret_key)
            self.fromemail = 'aspiring.investments@gmail.com'
        else:
            self.amazonSes = None
            self.fromemail = ""

        self.db = None
        #self.db = tornado.database.Connection(
        #    host=options.mysql_host, database=options.mysql_database,
        #    user=options.mysql_user, password=options.mysql_password)
        memcache_host = os.environ.get('MEMCACHE_HOST', '127.0.0.1')
        self.mc = memcache.Client([memcache_host], debug=0)

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



class HomeHandler(BaseHandler):
    def get(self):
        if self.get_current_user():
            self.redirect("/save")
            return
        self.redirect("/login")
        #self.render("sheetshome.html")
        #self.render("testtouch.html")

class UserLoginHandler(BaseHandler):
    def get(self):
        # send the login/pw page
        argument = {}
        self.clear_cookie("user")        
        argument['user'] = None       
        self.render("userlogin.html", argument=argument)
    def post(self):
        # verify user login
        user = self.get_argument('email')
        password = self.get_argument('password')
        logging.info(user)
        if cloud.authenticate.user.authenticate_user(user,password):
            print("authenticate succeeded")
            self.set_current_user(user)
            self.redirect("/save")
        else:
            print("authenticate failed")
            self.redirect("/login")

class UserLogoutHandler(BaseHandler):
    def get(self):
        self.clear_cookie("user")
        self.redirect("/login")
    def post(self):
        # verify user login
        pass
        
class UserRegisterHandler(BaseHandler):
    def get(self):
        # send the login/pw page
        self.clear_cookie("user")        
        argument = {}
        argument['user'] = None        
        self.render("userregister.html", argument=argument)
    def post(self):
        user = self.get_argument('email')
        password = self.get_argument('password')
        logging.info(user)
        if cloud.authenticate.user.user_exists(user):
            # user already exists
            argument = {}
            argument['user'] = None            
            argument['reguser'] = user
            self.render("userregister-exists.html", argument=argument)
            return
        cloud.authenticate.user.create_user(user,password)
        self.set_current_user(user)        
        argument = {}
        argument['user'] = user
        self.render("userregister-ok.html", argument=argument)
        pass

class UserLostPasswordHandler(BaseHandler):
    def get(self):
        # send email with a password reset link
        argument = {}
        argument['user'] = None        
        self.render("lostpassword.html", argument=argument)        
    def post(self):
        user = self.get_argument('email')
        logging.info(user)        
        #verify user exists
        if not cloud.authenticate.user.user_exists(user):
            argument = {}
            argument['user'] = None
            argument['reguser'] = user
            self.render("lostpassword-baduser.html",  argument=argument)
            return
        # send email with a pw reset link
        self.sendLostPwEmail(user)
        argument = {}
        argument['user'] = None
        argument['reguser'] = user
        self.render("lostpassword-sentemail.html",  argument=argument)        
    def sendLostPwEmail(self, user):
        dongle = self.get_random_string(20)
        cloud.authenticate.user.set_user_dongle(user,dongle)
        link = self.getLostPwLink(user,dongle)        
        msg = "Please click the folloing link \n to reset password for user %s\n%s"%(user,link)
        logging.info(msg)
        message = EmailMessage()
        message.subject = 'reset password '
        message.bodyText = msg
        self.application.amazonSes.sendEmail(self.application.fromemail, user, message)        
    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,size))
    def getLostPwLink(self, user,dongle):
        return "http://"+self.request.host+"/pwreset?u="+user+"&d="+dongle

class PwResetHandler(BaseHandler):
    def get(self):
        # send email with a password reset link
        user = self.get_argument('u')
        dongle = self.get_argument('d')
        logging.info(user)
        logging.info(dongle)
        if (not (dongle == None)) and (dongle == cloud.authenticate.user.get_user_dongle(user)):
            argument = {}
            argument['user'] = None
            argument['reguser'] = user
            self.render("pwreset.html",  argument=argument)
            return
        argument = {}
        argument['user'] = None
        argument['reguser'] = user
        self.render("pwreset-invalid.html", argument=argument)        
    def post(self):
        user = self.get_argument('email')
        password = self.get_argument('password')
        logging.info(user)
        if not cloud.authenticate.user.user_exists(user):
            # user does not exist
            argument = {}
            argument['user'] = None            
            argument['reguser'] = user
            self.render("lostpassword-baduser.html",  argument=argument)            
            return
        cloud.authenticate.user.update_password(user,password)
        argument = {}
        argument['user'] = None
        argument['reguser'] = user
        self.render("pwreset-ok.html", argument=argument)
        pass


class RunAsEmailHandler(BaseHandler):
    def post(self):
        user = self.get_current_user()
        if user == None:
            #this cannot happen
            self.redirect("/dev")
            return
        to = self.get_argument("to")
        content = self.get_argument("data")
        #subject = self.get_argument("appname")
        message = EmailMessage()
        message.subject = self.get_argument("subject")
        if message.subject == '':
            message.subject = user + " has shared "+self.get_argument("appname")

        message.bodyHtml = '<div><p>'+self.get_argument("text")+'</p></div>'+content

        logging.info(to)
        #logging.info(subject)
        #logging.info(content)                
        self.application.amazonSes.sendEmail(self.application.fromemail, to, message)
        self.finish(dict(data=to))        

class RunAsHandler(BaseHandler):
    def get(self):
        user = self.get_current_user()
        if user == None:
            #this cannot happen
            self.redirect("/dev")
            return                    
        sheets = self.get_argument("sheets")
        if not sheets:
            return
        else:
            sheets = urllib.parse.unquote(sheets)
            logging.info(sheets)
            lis = sheets.split(",")
            
        fname = self.get_argument("file")
        logging.info(self.request.arguments)

        path = ["home",user,fname]        

        fileobj = cloud.storage.storage.getFile(path)
        if (fileobj == None):
            logging.info("File not found "+fname)
            return
        entry = {}
        entry['fname'] = fname
        entry['sheetstr'] = fileobj.data
        entry['sheetmscestr'] = ""
        entry['sheets'] = lis

        if (self.get_cookie('dbLogin')) == '1':
        	entry['dbLogin'] = 1
        else:
        	entry['dbLogin'] = 0
        self.set_cookie("appUrl",self.request.uri)


        self.render("runappios43c.html", entry=entry)

        
class SaveHandler(BaseHandler):
    def get(self):
        # display all sheets
        user = self.get_current_user()
        if user == None:
            #this cannot happen
            self.redirect("/dev")
            return            
        path = ["home",user]
        dirobj = cloud.storage.storage.getFile(path)
        if (not dirobj) or (len(dirobj.files) == 0):
            logging.info("no directory")
            if not cloud.storage.storage.getFile(["home"]):
                cloud.storage.storage.createDir(["home"])
            cloud.storage.storage.createDir(path)
            filedata = {}
            filedata["user"] = user
            filedata["fname"] = "default"
            filedata["data"] = "\n"
            fpath = path[:]
            fpath.append("default")
            logging.info(fpath)
            cloud.storage.storage.createFile(fpath,json.dumps(filedata))
            dirobj = cloud.storage.storage.getFile(path)
        entries = dirobj.files
        logging.info(entries)
        argument = {}
        argument['user'] = self.get_current_user()
        logging.info("done")
        argument['entries'] = entries
        logging.info(str(argument['entries']))        
        self.render("allusersheets.html", argument=argument)

        
    def post(self):
        user = self.get_current_user()
        if user == None:
            #this cannot happen
            self.redirect("/dev")
            return
        fname = self.get_argument('fname')
        logging.info("fname is "+fname)
        sheetstr = self.get_argument("data", None)
        path = ["home",user,fname]
        if sheetstr != None:
            fileobj = cloud.storage.storage.getFile(path)
            if fileobj == None:
                cloud.storage.storage.createFile(path,sheetstr)                
            else:
                cloud.storage.storage.updateFile(path,sheetstr)                
        self.finish(dict(data="Done"))        

class SearchHandler(BaseHandler):
    def get(self):
        user = self.get_current_user()
        if user == None:
            #this cannot happen
            self.redirect("/dev")
            return            
        query = self.get_argument("q", "").strip()
        path = ["home", user]
        entries = cloud.storage.storage.searchFiles(path, query)
        argument = {}
        argument["user"] = user
        argument["entries"] = entries
        self.render("allusersheets.html", argument=argument)

class WebAppHandler(BaseHandler):
    # add error cases also
    def get_user_id(self):
        return self.get_argument("uuid")
    def get(self):
        # display all sheets
        action = self.get_argument('action')
        # check login or no
        if action == "login":
            user = self.get_current_user()
            if user == None:
               #this cannot happen
               self.finish(dict(result="fail"))
               return
            logging.info("user "+user)
            self.finish(dict(result="ok"))
        if action == "getInapp":
            app = self.get_argument('appname')
            user = self.get_current_user()
            if user == None:
               #this cannot happen
               self.finish(dict(result="fail"))
               return
            dirpath = ["home",user,"securestore","inapp"]
            path = ["home",user,"securestore","inapp", app]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                print("no directory found, no inapp initialised")
                self.finish(dict(result="no"))
                return
            fileobj = cloud.storage.storage.getFile(path)
            if fileobj == None:
                self.finish(dict(result="no"))
                return
            else:
                filedata = fileobj.data
                #logging.info(filedata)
                self.finish(dict(result=filedata))
                return

    def get_no_of_files_left(self,app):
        user = self.get_current_user()
        logging.info(app)
        dirpath = ["home",user,"securestore","inapp"]
        path = ["home",user,"securestore","inapp", app]
        dirobj = cloud.storage.storage.getFile(dirpath)
        if (not dirobj) or (len(dirobj.files) == 0):
            print("no directory found, creating..")
            cloud.storage.storage.createDir(dirpath)
        # dir is now created
        fileobj = cloud.storage.storage.getFile(path)
        if fileobj == None:
            message ={}
            message['own'] = 5
            message['consumed'] = 0
            logging.info(json.dumps(message))
            cloud.storage.storage.createFile(path,message)
            save_count = message['own'] - message['consumed']
            #logging.info(save_count)
            return save_count
        else:
            filedata = fileobj.data
            #logging.info(filedata)
            message = {}
            message['own'] = filedata['own']
            message['consumed'] = filedata['consumed']
            save_count = message['own'] - message['consumed']
            #logging.info(save_count)
            return save_count
            
    def post(self):
        action = self.get_argument('action')
        # save file starts
        if action == "savefile":
            #time.sleep(240)
            user = self.get_current_user()
            if user == None:
            #this cannot happen
                self.finish(dict(result="fail"))
                return
            logging.info(action)
            logging.info("user is "+user)
            appname = self.get_argument('appname')
            fname = self.get_argument('fname')     
            #footers = self.get_argument('footers')   
            logging.info("appname is "+appname)
            logging.info("fname is "+fname) 
            #logging.info("footers are "+footers)        
            sheetstr = self.get_argument("data", None)
            # get save count
            no_of_files_left = self.get_no_of_files_left(appname)
            if no_of_files_left <=0 :
                self.finish(dict(result="buy"))
            else:
                #continue saving file
                path = ["home",user,"securestore",appname,fname]
                #if directory does not exist, create it
                dirpath = ["home",user,"securestore",appname]
                dirobj = cloud.storage.storage.getFile(dirpath)
                if (not dirobj) or (len(dirobj.files) == 0):
                    logging.info("no directory")
                    cloud.storage.storage.createDir(dirpath)
                #dir is now created
                if sheetstr != None:
                    fileobj = cloud.storage.storage.getFile(path)
                    if fileobj == None:
                       cloud.storage.storage.createFile(path,sheetstr)
                       self.finish(dict(result="ok"))
                    else:
                       cloud.storage.storage.updateFile(path,sheetstr)
                       self.finish(dict(result="ok"))
        # save current file
        if action == "savecurrentfile":
            user = self.get_current_user()
            if user == None:
            #this cannot happen
                self.finish(dict(result="fail"))
                return
            logging.info(action)
            logging.info("user is "+user)
            appname = self.get_argument('appname')
            fname = self.get_argument('fname')     
            #footers = self.get_argument('footers')   
            logging.info("appname is "+appname)
            logging.info("fname is "+fname) 
            #logging.info("footers are "+footers)        
            sheetstr = self.get_argument("data", None)
            path = ["home",user,"securestore",appname,fname]
            #if directory does not exist, create it
            dirpath = ["home",user,"securestore",appname]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                logging.info("no directory")
                self.finish(dict(result="fail"))
            if sheetstr != None:
                fileobj = cloud.storage.storage.getFile(path)
                if fileobj == None:
                    #cloud.storage.storage.createFile(path,sheetstr)
                    self.finish(dict(result="fail"))                
                else:
                    cloud.storage.storage.updateFile(path,sheetstr)                
                    self.finish(dict(result="ok"))
        if action == "getfile":
            user = self.get_current_user()
            if user == None:
                #this cannot happen
                self.finish(dict(result="fail"))
                return
            logging.info("user is "+user)
            appname = self.get_argument('appname')
            fname = self.get_argument('fname')        
            logging.info("appname is "+appname)
            logging.info("fname is "+fname)                    
            path = ["home",user,"securestore",appname,fname]
            fileobj = cloud.storage.storage.getFile(path)
            if (fileobj == None):
                logging.info("File not found "+fname)
                self.finish(dict(result="fail"))
                return
            self.finish(dict(data=fileobj.data,result="ok"))
        if action == "deletefile":
            user = self.get_current_user()
            if user == None:
                #this cannot happen
                self.finish(dict(result="fail"))
                return
            logging.info("user is "+user)
            appname = self.get_argument('appname')
            fname = self.get_argument('fname')        
            logging.info("appname is "+appname)
            logging.info("fname is "+fname)
            path = ["home",user,"securestore",appname,fname]            
            cloud.storage.storage.deleteFile(path)
            self.finish(dict(result="ok"))            
        if action == "listdir":
            user = self.get_current_user()
            if user == None:
                #this cannot happen
                self.finish(dict(result="fail"))
                return
            logging.info("user is "+user)
            appname = self.get_argument('appname')
            logging.info("appname is "+appname)
            path = ["home",user,"securestore",appname]            
            dirobj = cloud.storage.storage.getFile(path)
            if (not dirobj) or (len(dirobj.files) == 0):
                logging.info("no directory")
                cloud.storage.storage.createDir(path)
                dirobj = cloud.storage.storage.getFile(path)
            entries = [i.fname for i in dirobj.files]
            self.finish(dict(data=entries,result="ok"))
        if action == "login":
            user = self.get_user_id()
            password = self.get_argument('password')
            app = self.get_argument('appname')
            if cloud.authenticate.user.authenticate_user(user, password):
                self.set_current_user(user)
                device = self.get_argument('deviceId')
                path1 = ["home",user,"securestore","device"]
                dirpath = ["home",user,"securestore"]
                dirobj1 = cloud.storage.storage.getFile(dirpath)
                if (not dirobj1) or (len(dirobj1.files) == 0):
                   cloud.storage.storage.createDir(dirpath)
                fileobj1 = cloud.storage.storage.getFile(path1)
                if fileobj1 == None:
                   cloud.storage.storage.createFile(path1,device)
                else:
                    filedata = fileobj1.data
                    filesdata = filedata.split(',')
                    ctr = 0
                    for i in range(0,len(filesdata)):
                        fnme = filesdata[i]
                        if device == fnme:
                           ctr+=1
                    if ctr==0:
                        device += ","+fileobj1.data
                        cloud.storage.storage.updateFile(path1,device)
                path = ["home",user,"securestore","ios"]
                dirobj = cloud.storage.storage.getFile(dirpath)
                if (not dirobj) or (len(dirobj.files) == 0):
                   cloud.storage.storage.createDir(dirpath)
                if app != None:
                   fileobj = cloud.storage.storage.getFile(path)
                   if fileobj == None:
                      cloud.storage.storage.createFile(path,app)
                      self.finish(dict(result="ok"))
                   else:
                      filedata = fileobj.data
                      filesdata = filedata.split(',')
                      for i in range(0,len(filesdata)):
                        fnme = filesdata[i]
                        if app == fnme:
                           self.finish(dict(result="ok"))
                           return
                      app += ","+fileobj.data
                      cloud.storage.storage.updateFile(path,app)
                      self.finish(dict(result="ok"))
            else:
                self.finish(dict(result="fail", data="authfail"))
        if action == "logout":
            self.clear_cookie("user")
            self.finish(dict(result="ok"))
        if action == "register":
            user = self.get_user_id()
            password = self.get_argument('password')
            if cloud.authenticate.user.user_exists(user):
                self.finish(dict(result="exist"))
                return
            cloud.authenticate.user.create_user(user, password)
            # Verify the S3 write landed before proceeding
            if not cloud.authenticate.user.user_exists(user):
                logging.error("register: user not found in S3 after create_user for %s", user)
                self.finish(dict(result="fail", data="registration_storage_error"))
                return
            self.set_current_user(user)
            app = self.get_argument("appname")
            path = ["home",user,"securestore","ios"]
            dirpath = ["home",user,"securestore"]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
               cloud.storage.storage.createDir(dirpath)
            if app != None:
               fileobj = cloud.storage.storage.getFile(path)
               if fileobj == None:
                  cloud.storage.storage.createFile(path,app)
                  self.finish(dict(result="ok"))
               else:
                  filedata = fileobj.data
                  filesdata = filedata.split(',')
                  for i in range(0,len(filesdata)):
                    fnme = filesdata[i]
                    if app == fnme:
                       self.finish(dict(result="ok"))
                       return
                  app += ","+fileobj.data
                  cloud.storage.storage.updateFile(path,app)
                  self.finish(dict(result="ok"))

        if action == "inapp":
            user = self.get_current_user()
            if user == None:
                #this cannot happen
                self.finish(dict(result="fail"))
                return
            logging.info("user is "+user)
            logging.info("user "+user)
            appname= self.get_argument("appname")
            fname = "inapp"
            logging.info("fname is "+fname)
            device = self.get_argument("device")
            logging.info("device is "+device)
            sheetstr = self.get_argument("inappname")
            if device == "iPhone" or device == "iPod":
                sheetstr+="p"
            logging.info("inappname "+sheetstr)
            path = ["home",user,"securestore","ios",fname]
            dirpath = ["home",user,"securestore","ios"]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                logging.info("no directory ")
                cloud.storage.storage.createDir(dirpath)
            #dir created
            logging.info("path is "+str(path))
            if sheetstr != None:
                fileobj = cloud.storage.storage.getFile(path)
                if fileobj == None:
                    cloud.storage.storage.createFile(path,sheetstr)
                    self.finish(dict(result="ok"))                
                else:
                    filedata = fileobj.data
                    filesdata = filedata.split(',')
                    #logging.info(filesdata)
                    for i in range(0,len(filesdata)):
                        fnme = filesdata[i]
                        if sheetstr == fnme:
                           self.finish(dict(result="already purchased"))
                           return
                    sheetstr += ","+fileobj.data
                    logging.info(sheetstr)
                    cloud.storage.storage.updateFile(path,sheetstr) 
                    self.finish(dict(result="ok")) 
        # update save count
        if action == "update":
            user = self.get_current_user()
            if user == None:
              #this cannot happen
              self.finish(dict(result="fail"))
              return
            logging.info("user is: "+user)
            appname = self.get_argument('appname')
            logging.info("appname is"+appname)
            dirpath = ["home",user,"securestore","inapp"]
            path = ["home",user,"securestore","inapp",appname]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                print("no directory found, creating..")
                cloud.storage.storage.createDir(dirpath)
            # dir is now created
            fileobj = cloud.storage.storage.getFile(path)
            if fileobj == None:
                message = {} 
                message['own'] = 5 
                message['consumed'] = 0
                #logging.info(json.dumps(message))
                cloud.storage.storage.createFile(path,message)
                logging.info(message)
                self.finish(dict(result="ok"))
            else:
                filedata = fileobj.data
                consumed = filedata['consumed']
                print("consumed was ",consumed)
                consumed += 1
                print("consumed now ", consumed)
                message = {}
                message['consumed'] = consumed
                message['own'] = filedata['own']
                cloud.storage.storage.updateFile(path,message)
                logging.info(message)
                #self.finish(dict(result="ok"))
                if consumed == filedata['own']:
                    message['consumed'] = 0
                    message['own'] = 0
                    cloud.storage.storage.updateFile(path,message)
                    print("save exhausted " , message)
                    self.finish(dict(result="ok"))
                    return
                self.finish(dict(result="ok"))
        #inapp here
        if action == "purchase":
            user = self.get_current_user()
            if user == None:
              #this cannot happen
              self.finish(dict(result="fail"))
              return
            logging.info("user is: "+user)
            appname = self.get_argument('appname')
            logging.info("appname is"+appname)
            path = ["home",user,"securestore","inapp",appname]
            fileobj = cloud.storage.storage.getFile(path)
            if fileobj == None:
                message = {} 
                message['own'] = 10
                message['consumed'] = 0
                #logging.info(json.dumps(message))
                cloud.storage.storage.createFile(path,message)
            else:
                message = {} 
                message['consumed'] = 0
                message['own'] = 10
                cloud.storage.storage.updateFile(path,message)
                logging.info(message)
                self.finish(dict(result="ok"))
            
            
            
class TemplatesHandler(BaseHandler):
    def getTemplate(self, sheetstr):
        index1 = sheetstr.index("cell:F107")
        index1 = sheetstr.index("\n",index1)
        index2 = sheetstr.index("--SocialCalcSpreadsheetControlSave",index1)
        return sheetstr[index1:index2]

    def post(self):
        sheetstr = self.get_argument("savespreadsheet", None)
        user = "demo"
        if sheetstr != None:
            model = self.getTemplate(self.get_argument("newstr"))
            #logging.info(model)
            fname = self.get_argument("newpagename")
            #differentiate the new template from existing template
            template = self.db.query("SELECT * FROM StockTemplates WHERE user = %s AND fname = %s",user,fname)            
            if len(template) == 0:
                self.db.execute(
                    "INSERT INTO StockTemplates (user,fname,data)"
                    " VALUES (%s,%s,%s)",
                    user, fname, model)
            else:
                self.db.execute(
                    "UPDATE StockTemplates SET data = %s"
                    "WHERE user = %s AND fname = %s", model, user, fname)
                
        entries = self.db.query("SELECT * FROM StockTemplates WHERE user = %s",user)
        if not entries:
            #create a default entry
            self.db.execute(
                "INSERT INTO StockTemplates (user,fname,data)"
                " VALUES (%s,%s,%s)",
                user, "Financial Statements", "\n")
            entries = self.db.query("SELECT * FROM StockTemplates WHERE user = %s",user)
        for i in entries:
            pass
            #logging.info(i.fname)
            #logging.info("----")
        argument = {}
        argument['ticker'] = self.get_argument("ticker")
        #logging.info(argument['ticker'])
        argument['entries'] = entries
        self.render("allstockpages.html", argument=argument)
        #self.write("StockHandler")        

class StockHandler(BaseHandler):
    def get(self):
        self.write("StockHandler")
    def post(self):
        savebeg = """
socialcalc:version:1.0
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary=SocialCalcSpreadsheetControlSave
--SocialCalcSpreadsheetControlSave
Content-type: text/plain; charset=UTF-8

# SocialCalc Spreadsheet Control Save
version:1.0
part:sheet
--SocialCalcSpreadsheetControlSave
Content-type: text/plain; charset=UTF-8

version:1.5

"""
        saveend = """

--SocialCalcSpreadsheetControlSave--

"""
        user = "demo"
        session = self.get_random_string(6)
        logging.info("session is %s"%session)
        self.set_cookie("session",session)
        self.set_cookie("idinsession",str(1))
        ticker = self.get_argument('ticker')
        fname = self.get_argument('pagename')
        cmdname = os.path.join(self.application.settings["util_path"],"msnparse.py")
        #logging.info("cmd is %s"%cmdname)
        sheetstr = subprocess.getoutput("python %s %s"%(cmdname,ticker))
        template = self.db.query("SELECT * FROM StockTemplates WHERE user = %s AND fname = %s",user,fname)
        #logging.info(sheetstr)
        #logging.info("---")
        #logging.info(template)
        if len(template) != 0:
            sheetstr = sheetstr+template[0].data
        entry = {}
        entry['sheetstr'] = savebeg+sheetstr+saveend
        #entry['sheetstr'] = ""
        entry['ticker'] = ticker
        entry['fname'] = fname
        entry['session'] = session
        # create the new session
        channels[session] = MessageMixin(session, ticker, fname)
        self.render("editstocksheet.html", entry=entry)
    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,6))

#
# This is where shared sessions start
#
class SharedSessionHandler(BaseHandler):
    def post(self):
        #self.write("Shared session for %s"%self.get_argument("sessionid"))
        session = self.get_argument("sessionid")
        channel = channels.get(session,None)
        if channel != None:
            entry = {}
            entry['ticker'] = channel.ticker
            entry['fname'] = channel.fname
            entry['session'] = session        
            entry['sheetstr'] = ""
            entry['sheetmscestr'] = ""            
            self.set_cookie("session",session)
            self.set_cookie("idinsession",str(channel.get_nextid()))
            #self.render("sharedstocksheet.html", entry=entry)
            #self.render("sharedmultistocksheet.html", entry=entry)
            self.render("importcollabload.html", entry=entry)
        else:
            self.write("ERROT: Shared session for %s Not Found"%session)



class MessageMixin:
    def __init__(self, session, ticker, fname):
        self.waiters = []
        self.cache = []
        self.cache_size = 1000
        self.session = session
        self.ticker = ticker
        self.fname = fname
        self.nextid = 2;

    async def wait_for_messages(self, cursor=None):
        if cursor:
            index = 0
            for i in range(len(self.cache)):
                index = len(self.cache) - i - 1
                if self.cache[index]["id"] == cursor:
                    break
            recent = self.cache[index + 1:]
            if recent:
                return recent
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)

        return await future

    def new_messages(self, message):
        logging.info("Sending new message to %r listeners", len(self.waiters))
        for future in self.waiters:
            try:
                if not future.done():
                    future.set_result(message)
            except Exception:
                logging.exception("Error in waiter future")
        self.waiters = []
        self.cache.extend(message)
        if len(self.cache) > self.cache_size:
            self.cache = self.cache[-self.cache_size:]

    def get_nextid(self):
        id = self.nextid;
        self.nextid = self.nextid+1
        return id

#
# this is where new broadcast messages come in
#
class MessageNewHandler(BaseHandler):
    def post(self):
        message = {
            "id": str(uuid.uuid4()),
            "idinsession": self.get_cookie("idinsession"),
            "session":self.get_cookie("session"),
            "data": self.get_argument("data"),
            "type": self.get_argument("type"),
            "from": self.get_argument("from"),
            "html": '<div></div>'
        }
        #logging.info(message)
        # write back some message
        self.write(message)
        # get the right channel and post a message to it
        session = self.get_cookie("session")
        channel = channels.get(session,None)
        if channel != None:
            #broadcast
            channel.new_messages([message])


#
# This is the long poller
#
class MessageUpdateHandler(BaseHandler):
    async def post(self):
        #create a new channel if id=1 and no channel exists
        cursor = self.get_argument("cursor", None)
        session = self.get_cookie("session")        
        id = self.get_cookie("idinsession")
        #logging.info("long poll id=%s,session=%s"%(id,session))
        channel = channels.get(session,None)
        if channel:
            messages = await channel.wait_for_messages(cursor=cursor)
            self.finish(dict(messages=messages))

    # def on_new_messages(self, messages):
    #     # Closed client connection
    #     if self.request.connection.stream.closed():
    #         return
    #     #logging.info(messages)
    #     #self.write(messages)
    #     self.finish(dict(messages=messages))


class MultiSheetHandler(BaseHandler):
    def get(self):
        self.write("StockHandler")
    def post(self):
        user = "demo"
        session = self.get_random_string(6)
        logging.info("session is %s"%session)
        self.set_cookie("session",session)
        self.set_cookie("idinsession",str(1))
        ticker = self.get_argument('ticker')
        fname = self.get_argument('pagename')
        cmdname = os.path.join(self.application.settings["util_path"],"msnparse.py")
        logging.info("cmd is %s"%cmdname)
        sheetstr = subprocess.getoutput("python %s %s"%(cmdname,ticker))
        #template = self.db.query("SELECT * FROM StockTemplates WHERE user = %s AND fname = %s",user,fname)
        #logging.info(sheetstr)
        #logging.info("---")
        #logging.info(template)
        #if len(template) != 0:
        #    sheetstr = sheetstr+template[0].data
        entry = {}
        #entry['sheetstr'] = savebeg+sheetstr+saveend
        entry['sheetstr'] = sheetstr
        entry['ticker'] = ticker
        entry['fname'] = fname
        entry['session'] = session
        # create the new session
        channels[session] = MessageMixin(session, ticker, fname)
        self.render("editmultistocksheet.html", entry=entry)

    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,6))


sessionfileuploads = {}



class UploadTestHandler(BaseHandler):
    def get(self):
        entry = {}
        entry['fname'] = "test"
        entry['sheetstr'] = ""
        self.render("uploadtest.html", entry=entry)

    def post(self):
        fname = self.request.files['upload'][0]['filename']
        fcontent = self.request.files['upload'][0]['body']
        fullfname = "./excelinterop/phpexcel/socialcalc/tmp/"+fname
        f = open(fullfname,"w")
        f.write(fcontent)
        f.close()
        #logging.info("wrote "+fullfname)
        cmdname = "./excelinterop/phpexcel/socialcalc/import.php"
        output = subprocess.getoutput("php %s %s"%(cmdname, fullfname))
        #logging.info("output is "+output)
        i = output.index("$---$")
        wbook = output[i+5:]
        sessionfileuploads[fname] = wbook

        #logging.info(fname)
        #logging.info(wbook)

        entry = {}
        entry['fname'] = fname
        entry['sheetstr'] = wbook

        self.render("uploadtest.html", entry=entry)        
        

        #def post(self):
        #sheetstr = open("/home/ramu/apps/phpexcel/1.7.5/Tests/socialcalc/testsc.txt").read()
        #import urllib
        #sheetstr = urllib.quote(sheetstr)
        #logging.info("cmd is %s"%cmdname)
        #sheetstr = commands.getoutput("python %s %s"%(cmdname,ticker))
        #logging.info(self.request)

        #fname = self.get_argument("fname",None)
        #sheetstr = ""
        #if fname != None:
        #    sheetstr = sessionfileuploads.get(fname,None)
        #    if sheetstr == None:
        #        sheetstr = ""
        #self.finish(dict(data=sheetstr))


class UploadHandler(BaseHandler):
    def get(self):
        entry = {}
        entry['fname'] = "test"
        entry['sheetstr'] = ""
        self.render("uploadtest.html",entry=entry)
    def post(self):
        fname = self.request.files['upload'][0]['filename']
        fcontent = self.request.files['upload'][0]['body']

        fullfname = "./excelinterop/phpexcel/socialcalc/tmp/"+fname
        f = open(fullfname,"w")
        f.write(fcontent)
        f.close()
        #logging.info("wrote "+fullfname)
        cmdname = "./excelinterop/phpexcel/socialcalc/import.php"
        output = subprocess.getoutput("php %s %s"%(cmdname, fullfname))
        #logging.info("output is "+output)
        i = output.index("$---$")
        wbook = output[i+5:]
        sessionfileuploads[fname] = wbook

        #logging.info(fname)
        #logging.info(wbook)

        entry = {}
        entry['fname'] = fname
        entry['sheetstr'] = wbook

        self.render("uploadtest.html", entry=entry)        


contenttypes = {"Excel2007":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Excel5":"application/vnd.ms-excel",
                "PDF":"application/pdf",
                "HTML":"text/html",
                "CSV":"text/plain",
                "MSC":"text/plain",
                "MSCE":"text/plain",
                "ODS":"application/vnd.oasis.opendocument.spreadsheet"}


suffix = {"Excel2007":"xlsx",
          "Excel5":"xls",
          "PDF":"pdf",
          "HTML":"html",
          "CSV":"csv",
          "ODS":"ods",
          "MSC":"msc",
          "MSCE":"msce"}

sessionfiledownloads = {}
import codecs
import tempfile
from excelinterop.socialcalc_interop import export_xlsx, export_xls, export_csv

class DownloadFileHandler(BaseHandler):
    def post(self):
        logging.info(self.get_argument("type"))
        type = self.get_argument('type')
        raw_content = self.get_argument('content')
        if type == "Excel2007":
            content = export_xlsx(raw_content)
        elif type == "Excel5":
            content = export_xls(raw_content)
        elif type == "CSV":
            content = export_csv(raw_content).encode('utf-8')
        elif type == "PDF":
            from excelinterop.socialcalc_interop import export_html
            logging.info("type is PDF")
            html_content = export_html(raw_content)
            tmpdir = tempfile.mkdtemp()
            inpfile = os.path.join(tmpdir, "tmp.html")
            outfile = os.path.join(tmpdir, "tmp.pdf")
            with open(inpfile, 'w', encoding='utf-8') as f:
                f.write(html_content)
            cmdname = "/usr/local/bin/wkhtmltopdf.sh"
            if not os.path.exists(cmdname):
                cmdname = "wkhtmltopdf"
            output = subprocess.getoutput(
                "%s --enable-javascript --javascript-delay 2000 --no-stop-slow-scripts %s %s"
                % (cmdname, inpfile, outfile)
            )
            logging.info(output)
            if os.path.exists(outfile):
                with open(outfile, 'rb') as f:
                    content = f.read()
            else:
                self.set_status(500)
                self.write("PDF generation failed: wkhtmltopdf not available or errored")
                return
        elif type == "HTML":
            from excelinterop.socialcalc_interop import export_html
            content = export_html(raw_content)
        elif type in ("MSC", "MSCE"):
            content = raw_content
        else:
            self.set_status(400)
            self.write("Unknown export type")
            return
        self.set_header("Content-Type", contenttypes[type])
        self.set_header("Content-Disposition", 'attachment;filename='+"tmp."+suffix[type])
        self.set_header("Cache-Control", 'max-age=0')
        self.write(content)


MESHKIT_BASE = "http://meshkit-service:4000"
MESHKIT_SIDECAR_URL = os.getenv("MESHKIT_SIDECAR_URL", "http://localhost:5050")
MESHKIT_KUBO_SIDECAR_URL = os.getenv("MESHKIT_KUBO_SIDECAR_URL", "http://localhost:5051")

class MeshkitHandler(BaseHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")

    def options(self):
        self.set_status(204)
        self.finish()

    @tornado.gen.coroutine
    def get(self):
        action = self.get_argument("action", "")
        http = tornado.httpclient.AsyncHTTPClient()
        try:
            if action == "getjson":
                cid = self.get_argument("cid")
                resp = yield http.fetch("%s/getJSON/%s" % (MESHKIT_BASE, cid))
                self.set_header("Content-Type", "application/json")
                self.write(resp.body)
            elif action == "getfile":
                cid = self.get_argument("cid")
                resp = yield http.fetch("%s/getFile/%s" % (MESHKIT_BASE, cid))
                self.set_header("Content-Type", resp.headers.get("Content-Type", "application/octet-stream"))
                self.write(resp.body)
            elif action == "delete":
                cid = self.get_argument("cid")
                req = tornado.httpclient.HTTPRequest(
                    "%s/delete/%s" % (MESHKIT_BASE, cid), method="DELETE"
                )
                resp = yield http.fetch(req)
                self.set_header("Content-Type", "application/json")
                self.write(resp.body)
            else:
                self.set_status(400)
                self.write({"error": "unknown action"})
        except tornado.httpclient.HTTPClientError as e:
            self.set_status(500)
            self.write({"error": str(e)})

    @tornado.gen.coroutine
    def post(self):
        action = self.get_argument("action", "")
        http = tornado.httpclient.AsyncHTTPClient()
        try:
            if action == "putjson":
                body = self.request.body
                req = tornado.httpclient.HTTPRequest(
                    "%s/putJSON" % MESHKIT_BASE, method="POST",
                    headers={"Content-Type": "application/json"}, body=body
                )
                resp = yield http.fetch(req)
                self.set_header("Content-Type", "application/json")
                self.write(resp.body)
            elif action == "putfile":
                body = self.request.body
                content_type = self.request.headers.get("Content-Type", "application/octet-stream")
                req = tornado.httpclient.HTTPRequest(
                    "%s/putFile" % MESHKIT_BASE, method="POST",
                    headers={"Content-Type": content_type}, body=body
                )
                resp = yield http.fetch(req)
                self.set_header("Content-Type", "application/json")
                self.write(resp.body)
            else:
                self.set_status(400)
                self.write({"error": "unknown action"})
        except tornado.httpclient.HTTPClientError as e:
            self.set_status(500)
            self.write({"error": str(e)})


class MeshkitSidecarHandler(BaseHandler):
    """Proxy handler for the meshkit-sidecar Node service (port 5050).
    Exposes REST-style routes: /meshkit/upload, /meshkit/retrieve/{cid}, /meshkit/list.
    """

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")

    def options(self, *args):
        self.set_status(204)
        self.finish()

    @tornado.gen.coroutine
    def post(self):
        # POST /meshkit/upload — forward raw bytes to sidecar
        http = tornado.httpclient.AsyncHTTPClient()
        try:
            req = tornado.httpclient.HTTPRequest(
                "%s/upload" % MESHKIT_SIDECAR_URL, method="POST",
                headers={"Content-Type": "application/octet-stream"},
                body=self.request.body or b""
            )
            resp = yield http.fetch(req)
            self.set_header("Content-Type", "application/json")
            self.write(resp.body)
        except tornado.httpclient.HTTPClientError as e:
            status = 502 if e.code == 599 else 500
            logging.error("meshkit-sidecar upload error: %s", e)
            self.set_status(status)
            self.write({"error": str(e)})

    @tornado.gen.coroutine
    def get(self, cid=None):
        # GET /meshkit/retrieve/{cid}  or  GET /meshkit/list
        http = tornado.httpclient.AsyncHTTPClient()
        try:
            if cid is not None:
                resp = yield http.fetch(
                    "%s/retrieve/%s" % (MESHKIT_SIDECAR_URL, cid)
                )
                self.set_header(
                    "Content-Type",
                    resp.headers.get("Content-Type", "application/octet-stream")
                )
                self.write(resp.body)
            else:
                resp = yield http.fetch("%s/list" % MESHKIT_SIDECAR_URL)
                self.set_header("Content-Type", "application/json")
                self.write(resp.body)
        except tornado.httpclient.HTTPClientError as e:
            if e.code == 404:
                self.set_status(404)
                self.write({"error": "not found"})
            else:
                status = 502 if e.code == 599 else 500
                logging.error("meshkit-sidecar retrieve/list error: %s", e)
                self.set_status(status)
                self.write({"error": str(e)})


class MeshkitKuboSidecarHandler(BaseHandler):
    """Proxy handler for the meshkit-sidecar-kubo Node service (port 5051).
    Kubo/IPFS-daemon backend — parallel to MeshkitSidecarHandler (S3 backend).
    Routes: /meshkit-kubo/upload, /meshkit-kubo/retrieve/{cid}, /meshkit-kubo/list
    """

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")

    def options(self, *args):
        self.set_status(204)
        self.finish()

    @tornado.gen.coroutine
    def post(self):
        # POST /meshkit-kubo/upload — forward raw bytes to sidecar
        http = tornado.httpclient.AsyncHTTPClient()
        try:
            req = tornado.httpclient.HTTPRequest(
                "%s/upload" % MESHKIT_KUBO_SIDECAR_URL, method="POST",
                headers={"Content-Type": "application/octet-stream"},
                body=self.request.body or b""
            )
            resp = yield http.fetch(req)
            self.set_header("Content-Type", "application/json")
            self.write(resp.body)
        except tornado.httpclient.HTTPClientError as e:
            # 599 = Tornado network error (sidecar unreachable) → 502
            # 504 = sidecar reported a timeout (e.g. Kubo retrieve hung) → propagate as 504
            # anything else → 500
            status = 502 if e.code == 599 else (504 if e.code == 504 else 500)
            logging.error("meshkit-kubo upload error: %s", e)
            self.set_status(status)
            self.write({"error": str(e)})

    @tornado.gen.coroutine
    def get(self, cid=None):
        # GET /meshkit-kubo/retrieve/{cid}  or  GET /meshkit-kubo/list
        http = tornado.httpclient.AsyncHTTPClient()
        try:
            if cid is not None:
                resp = yield http.fetch(
                    "%s/retrieve/%s" % (MESHKIT_KUBO_SIDECAR_URL, cid)
                )
                self.set_header(
                    "Content-Type",
                    resp.headers.get("Content-Type", "application/octet-stream")
                )
                self.write(resp.body)
            else:
                resp = yield http.fetch("%s/list" % MESHKIT_KUBO_SIDECAR_URL)
                self.set_header("Content-Type", "application/json")
                self.write(resp.body)
        except tornado.httpclient.HTTPClientError as e:
            if e.code == 404:
                self.set_status(404)
                self.write({"error": "not found"})
            else:
                # 599 = sidecar unreachable → 502
                # 504 = sidecar retrieve timed out → propagate as 504
                status = 502 if e.code == 599 else (504 if e.code == 504 else 500)
                logging.error("meshkit-kubo retrieve/list error: %s", e)
                self.set_status(status)
                self.write({"error": str(e)})


class IconImgHandler(BaseHandler):
    suffixes = {}
    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,size))
    def exists_in_storage(self,fname):
        return cloud.storage.storage.existsItem(fname, IMG_BUCKET)
    def get_from_storage(self,fname):
        return cloud.storage.storage.getItem(fname, IMG_BUCKET)
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")
    def options(self):
        self.set_status(204)
        self.finish()
    def get(self):
        logging.info("in iconimg get")
        fname = self.get_argument('fname')
        fullname = "/home/ubuntu/tmp/iconimg/%s"%fname
        inpfile = fullname
        logging.info("fname=%s"%inpfile)        
        content_type = None
        if fullname[-4:] == ".png":
            content_type = "image/png"
            logging.info(fullname);   
       	if fullname[-4:] == ".gif":
            content_type = "image/gif"
        if fullname[-5:] == ".jpeg" or fullname[-4:] == ".jpg":
            content_type = "image/jpeg"            
        if content_type:
            self.set_header("Content-Type",content_type)
            #self.write(open(inpfile).read())
            if os.path.exists(inpfile):
                logging.info("found %s on disk"%fname)
                with open(inpfile, "rb") as f:
                    self.write(f.read())
            else:
                data = self.get_from_storage(fname)
                if data:
                    logging.info("found %s in s3"%fname)
                    self.write(data)
                else:
                    self.write("Not Found")
        else:
            self.set_header("Content-Type","text/plain")
            self.write("Unknown image suffix")
            
    def post(self):
        logging.info("in iconimg post")

        while True:
            fname=self.get_random_string(20)
            fullfname = "/home/ubuntu/tmp/iconimg/%s"%fname
            if os.path.exists(fullfname):
                continue
            elif self.exists_in_storage(fname):
                continue
            else:
                break;

        os.makedirs(os.path.dirname(fullfname), exist_ok=True)
        s = self.get_argument('content')
        suffix = self.get_argument('suffix')
        inpfile = fullfname+"."+suffix
        typ, dat = s.split(',')
        logging.info(len(dat))
        img_bytes = base64.b64decode(dat)
        with open(inpfile, "wb") as f:
            f.write(img_bytes)
        fname = fname+"."+suffix
        if cloud.storage.storage.putItem(fname, img_bytes, IMG_BUCKET):
            logging.info("uploaded image %s to s3" % fname)
        else:
            logging.error("s3 upload failed for image %s; file only available on this instance" % fname)
        base = PUBLIC_BASE_URL if PUBLIC_BASE_URL else "http://" + self.request.host
        imgurl = "%s/iconimg?fname=%s" % (base, fname)
        self.finish(dict(imgurl=imgurl,result="ok"))

class HtmlToPdfHandler(BaseHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")
    def options(self):
        self.set_status(204)
        self.finish()
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
        action = self.get_argument('action', default=None)
        if action and action == "preview":
            fullfname = os.path.join(HTMLTOPDF_BASE,"preview",fname)%fname
            inpfile = fullfname+".pdf"
            logging.info("fname=%s"%inpfile)        
            self.set_header("Content-Type","application/pdf")
            if os.path.exists(inpfile):
                logging.info("found %s on disk"%fname)
                with open(inpfile, "rb") as f: 
                    self.write(f.read())
            else:
                self.write("Not Found")                
            return

        fullfname = os.path.join(HTMLTOPDF_BASE,"htmltopdf",fname)
        inpfile = fullfname+".pdf"
        logging.info("fname=%s"%inpfile)        
        self.set_header("Content-Type","application/pdf")
        if os.path.exists(inpfile):
            logging.info("found %s on disk"%fname)
            with open(inpfile, "rb") as f:
                self.write(f.read())
        else:
            data = self.get_from_storage(fname)
            if data:
                logging.info("found %s in s3"%fname)
                self.write(data)
            else:
                self.write("Not Found")
        
    def post(self):
        logging.info("in htmltopdf converter post")
        # run with wkhtmltopdf
        logging.info("type is PDF")

        action = self.get_argument('action', default=None)

        if action and action == "preview":
            while True:
                fname=self.get_random_string(20)
                fullfname = os.path.join(HTMLTOPDF_BASE,"preview",fname)%fname
                if os.path.exists(fullfname):
                    continue
                else:
                    break
        else:
            while True:
                fname=self.get_random_string(20)
                fullfname = os.path.join(HTMLTOPDF_BASE,"htmltopdf",fname)
                if os.path.exists(fullfname):
                    continue
                elif self.exists_in_storage(fname):
                    continue
                else:
                    break

        if action and action == "send":
            # save the metadata file also
            uuid = self.get_argument('uuid',default=None)
            appname = self.get_argument('appname',default=None)
            filename = self.get_argument('filename', default=None)
            created = time.strftime("%Y:%m:%d %H:%M:%S")
            jsondata = {"uuid":uuid, "appname": appname, "filename": filename, "created": created}
            jsonstr = json.dumps(jsondata)
            jsonfile = fullfname+".json"
            f = open(jsonfile, "w")
            f.write(jsonstr)
            f.close()            

        os.makedirs(os.path.dirname(fullfname),exist_ok=True)
        inpfile = fullfname+".html"
        s = self.get_argument('content')

        #logging.info(s)
        f = open(inpfile,"w")
        f.write(s)
        f.close()
        outfile = fullfname+".pdf"
        logging.info(outfile)
        logging.info(inpfile)
        cmdname = "/usr/local/bin/wkhtmltopdf.sh"
        output = subprocess.getoutput("%s %s %s"%(cmdname, inpfile, outfile))
        if not os.path.exists(outfile):
            logging.error("wkhtmltopdf produced no output for %s: %s" % (fname, output))
        else:
            with open(outfile, "rb") as f:
                pdf_bytes = f.read()
            if cloud.storage.storage.putItem(fname, pdf_bytes, PDF_BUCKET):
                logging.info("uploaded pdf %s to s3" % fname)
            else:
                logging.error("s3 upload failed for %s; pdf only available on this instance" % fname)
        base = PUBLIC_BASE_URL if PUBLIC_BASE_URL else "http://" + self.request.host
        if action:
            pdfurl = "%s/htmltopdf?fname=%s&action=%s" % (base, fname, action)
        else:
            pdfurl = "%s/htmltopdf?fname=%s" % (base, fname)
        self.finish(dict(pdfurl=pdfurl,result="ok"))


class DownloadHandler(BaseHandler):
    def post(self):
        #logging.info(self.get_argument('type'))
        type = self.get_argument('type')
        #logging.info(self.get_argument('content'))
        fullfname = "./excelinterop/phpexcel/socialcalc/tmp/tmp"
        inpfile = fullfname+".b"
        f = open(inpfile,"wb")
        f.write(self.get_argument('content'))
        f.close()
        outfile = fullfname+"."+suffix[type]
        logging.info(outfile)
        logging.info(inpfile)
        cmdname = "./excelinterop/phpexcel/socialcalc/export.php"
        output = subprocess.getoutput("php %s %s %s %s"%(cmdname, inpfile, outfile, type))
        logging.info(output)
        sessionfiledownloads["file"] = outfile
        sessionfiledownloads["type"] = type
        self.finish(dict(data=type))

class ImportHandler(BaseHandler):
    def get(self):

        user = self.get_current_user()
        session = self.get_random_string(6)
        logging.info("session is %s"%session)
        self.set_cookie("session",session)
        self.set_cookie("idinsession",str(1))
        entry = {}
        entry['fname'] = "test"
        entry['sheetstr'] = ""
        entry['sheetmscestr'] = ""                    
        entry['session'] = session
        channels[session] = MessageMixin(session, "", "")
        self.render("importcollab.html", entry=entry)

    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,6))


    def post(self):
        from excelinterop.socialcalc_interop import import_xlsx, import_xls, import_csv

        session = self.get_cookie("session")

        fname = self.request.files['upload'][0]['filename']
        fcontent = self.request.files['upload'][0]['body']
        fname_lower = fname.lower()

        if fname_lower.endswith('.msc') or fname_lower.endswith('.msce'):
            wbook = fcontent.decode('utf-8', errors='replace') if isinstance(fcontent, bytes) else fcontent
        elif fname_lower.endswith('.xlsx'):
            wbook = import_xlsx(fcontent)
        elif fname_lower.endswith('.xls'):
            wbook = import_xls(fcontent)
        elif fname_lower.endswith('.csv'):
            wbook = import_csv(fcontent)
        else:
            wbook = import_xlsx(fcontent)

        sessionfileuploads[fname] = wbook

        self.set_cookie("idinsession", str(1))

        entry = {}
        entry['fname'] = fname
        if fname_lower.endswith('msce'):
            import urllib.parse
            if wbook.startswith('%7B') or wbook.startswith('%7b'):
                entry['sheetmscestr'] = wbook
            else:
                entry['sheetmscestr'] = urllib.parse.quote(wbook, safe='')
            entry['sheetstr'] = ""
        else:
            entry['sheetmscestr'] = ""
            entry['sheetstr'] = wbook

        entry['session'] = session
        self.render("importcollabload.html", entry=entry)



class TickerJsonHandler(BaseHandler):
    def post(self):
        tick1 = self.get_argument('tick1')
        tick2 = self.get_argument('tick2')
        tick3 = self.get_argument('tick3')

        # verify ticker is valid
        if ( (util.tickersymbols.isValidTicker(tick1) != True) or \
           (util.tickersymbols.isValidTicker(tick2) != True) or \
           (util.tickersymbols.isValidTicker(tick3) != True)):
            self.finish(dict(result="fail"))
            return

        
        logging.info("ticker is ",tick1,tick2,tick3)
        cmdname = os.path.join(self.application.settings["util_path"],"msnparse.py")
        logging.info("cmd is %s"%cmdname)
        sheetstr = subprocess.getoutput("python %s %s %s %s %s"%(cmdname,"json",tick1,tick2,tick3))
        self.finish(dict(data=sheetstr,result="ok"))        


class TickerHandler(BaseHandler):
    def post(self):
        ticker = self.get_argument('ticker')
        logging.info("ticker is "+ticker)

        # verify ticker is valid
        if util.tickersymbols.isValidTicker(ticker) != True:
            #self.finish(dict(result="fail"))
            #return
            logging.info("couldnt find ticker "+ticker);

        cmdname = os.path.join(self.application.settings["util_path"],"msnparse.py")
        logging.info("cmd is %s"%cmdname)
        sheetstr = subprocess.getoutput("python %s %s %s"%(cmdname,"none",ticker))
        #sheetstr = util.simpledb.getFromSimpleDb(ticker)
        tickdata = util.ystockquote.get_all(ticker)
        logging.info(tickdata)
        self.finish(dict(data=sheetstr,tick=tickdata,result="ok"))        

class TenYearDataHandler(BaseHandler):
    def post(self):
        ticker = self.get_argument('ticker')
        logging.info("ticker is "+ticker)

        # verify ticker is valid
        if util.tickersymbols.isValidTicker(ticker) != True:
            self.finish(dict(result="fail"))
            return

        cmdname = os.path.join(self.application.settings["util_path"],"tenyeardata.py")
        logging.info("cmd is %s"%cmdname)
        sheetstr = subprocess.getoutput("python %s %s"%(cmdname,ticker))
        self.finish(dict(data=sheetstr,result="ok"))        

class InsertHandler(BaseHandler):
    def post(self):
        user = self.get_current_user()
        if user == None:
            #this cannot happen
            self.redirect("/dev")
            return                    
        
        filename = self.get_argument('filename')

        logging.info(self.request.arguments)

        path = ["home",user,filename]

        logging.info("insert filename is "+filename)


        wbook = cloud.storage.storage.getFile(path)
        if wbook == None:
            logging.info("failed finding file");
            self.finish(dict(data="fail",result="fail"))
        else:
            logging.info("found file");            
            self.finish(dict(data=wbook.data,result="ok"))                            



class ShareHandler(BaseHandler):
    def post(self):
        pretext = """
Brought to you by Aspiring Investments
-----------------------------------------
Please click the following link to see the
shared investment model
"""             
        fname = self.get_random_string(20)
        logging.info("fname is "+fname)
        #logging.info("From "+self.get_argument("from"))
        fromname = self.get_argument("from")
        to = self.get_argument("to")
        msg = self.get_argument("msg")
        sheetstr = self.get_argument("data", None)
        user = "demo"
        if sheetstr != None:
            sheets = self.db.query("SELECT * FROM SharedSheets WHERE user = %s AND fname = %s",user,fname)            
            if len(sheets) == 0:
                self.db.execute(
                    "INSERT INTO SharedSheets (user,fname,data)"
                    " VALUES (%s,%s,%s)",
                    user, fname, sheetstr)
            else:
                pass
                #self.db.execute(
                #    "UPDATE UserSheets SET data = %s"
                #    "WHERE user = %s AND fname = %s", sheetstr, user, fname)
        link = "http://"+self.request.host+"/embed?arg="+fname
        # send email
        if (msg != ""):
            msg = pretext+"\n"+link+"\n\nMessage From "+fromname+ \
            "\n-----------------------------------------\n"+msg
        else:
            msg = pretext+"\n"+link+"\n"

        message = EmailMessage()
        message.subject = fromname+' has shared an investment model'
        message.bodyText = msg
        logging.info("From "+self.application.fromemail)
        logging.info("To "+ to)
        logging.info(message.subject)
        logging.info(message.bodyText)

        self.application.amazonSes.sendEmail(self.application.fromemail, to, message)

        
        self.finish(dict(data=to))        

    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,size))


#
# This is where shared sessions start
#
class CollaborateHandler(BaseHandler):
    def get(self, slug):
        #self.write("Shared session for %s"%self.get_argument("sessionid"))
        session = self.get_argument("shsessionid")
        channel = channels.get(session,None)
        if channel != None:
            entry = {}
            entry['ticker'] = channel.ticker
            entry['fname'] = channel.fname
            entry['session'] = session        
            entry['sheetstr'] = ""
            entry['sheetmscestr'] = ""            
            self.set_cookie("session",session)
            self.set_cookie("idinsession",str(channel.get_nextid()))
            #self.render("sharedstocksheet.html", entry=entry)
            #self.render("sharedmultistocksheet.html", entry=entry)
            self.render("importcollabload.html", entry=entry)
        else:
            self.write("ERROT: Shared session for %s Not Found"%session)


    def post(self, slug):
        pretext = """
Brought to you by Aspiring Investments
-----------------------------------------
Please click the following link to collaborate
real-time
"""             
        fromname = self.get_argument("from")
        to = self.get_argument("to")
        msg = self.get_argument("msg")
        session = self.get_argument("session", None)
        link = "http://"+self.request.host+"/collaborate?shsessionid="+session
        # send email
        if (msg != ""):
            msg = pretext+"\n"+link+"\n\nMessage From "+fromname+ \
            "\n-----------------------------------------\n"+msg
        else:
            msg = pretext+"\n"+link+"\n"

        message = EmailMessage()
        message.subject = fromname+' wants to collaborate on an investment model'
        message.bodyText = msg
        logging.info("From "+self.application.fromemail)
        logging.info("To "+ to)
        logging.info(message.subject)
        logging.info(message.bodyText)

        self.application.amazonSes.sendEmail(self.application.fromemail, to, message)

        
        self.finish(dict(data=to))        




class EmbedHandler(BaseHandler):
    def get(self,slug):
        #logging.info("in get embed"+slug)
        #logging.info(self.request.uri)
        #logging.info(self.request.host)
        #logging.info(self.get_argument("arg"))
        #self.write("Hello")

        user = "demo"
        fname = self.get_argument("arg")
        session = self.get_random_string(6)
        logging.info("session is %s"%session)

        self.set_cookie("session",session)
        self.set_cookie("idinsession",str(1))
        
        wbook = self.db.query("SELECT * FROM SharedSheets WHERE user = %s AND fname = %s",user,fname)
        #logging.info(wbook)
        entry = {}
        entry['fname'] = fname
        entry['sheetstr'] = wbook[0].data
        entry['sheetmscestr'] = ""
        entry['session'] = session
        channels[session] = MessageMixin(session, "", "")
        self.render("importcollabload.html", entry=entry)

        

    def post(self,slug):
        fname = self.get_random_string(20)
        logging.info("fname is "+fname)
        sheetstr = self.get_argument("data", None)
        user = "demo"
        if sheetstr != None:
            sheets = self.db.query("SELECT * FROM SharedSheets WHERE user = %s AND fname = %s",user,fname)            
            if len(sheets) == 0:
                self.db.execute(
                    "INSERT INTO SharedSheets (user,fname,data)"
                    " VALUES (%s,%s,%s)",
                    user, fname, sheetstr)
            else:
                pass
                #self.db.execute(
                #    "UPDATE UserSheets SET data = %s"
                #    "WHERE user = %s AND fname = %s", sheetstr, user, fname)
        link = "http://"+self.request.host+self.request.uri+"?arg="+fname
        self.finish(dict(data=link))        

    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,size))


        
class UserSheetHandler(BaseHandler):
    def post(self):
        user = self.get_current_user()
        if user == None:
            #this cannot happen
            self.redirect("/dev")
            return                    

        fname = self.get_argument("pagename")
        logging.info(self.request.arguments)

        path = ["home",user,fname]
        #if delete is set, delete the sheet
        isdel = self.get_argument("delete")
        if (isdel == "yes"):
            logging.info("deleting "+fname)
            cloud.storage.storage.deleteFile(path)
            self.redirect("/save")
            return

        session = self.get_random_string(6)
        logging.info("session is %s"%session)

        self.set_cookie("session",session)
        self.set_cookie("idinsession",str(1))
        
        #wbook = self.db.query("SELECT * FROM UserSheets WHERE user = %s AND fname = %s",user,fname)
        #logging.info(wbook)
        fileobj = cloud.storage.storage.getFile(path)
        if (fileobj == None):
            logging.info("File not found "+fname)
            return
        entry = {}
        entry['fname'] = fname
        entry['sheetstr'] = fileobj.data
        entry['sheetmscestr'] = ""
        entry['session'] = session
        channels[session] = MessageMixin(session, "", "")
        self.render("importcollabload.html", entry=entry)
        
    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,6))

class DropBoxHandler(BaseHandler):
    
    # Test code for automatic redirection from auth URL

    def get_dropbox_auth_flow(self, sessionid, csrftok=None):
        import dropbox
        redirect_uri = "https://%s"%(self.request.host)+"/dropbox?action=dropbox-auth-finish"
        #redirect_uri = "/dropbox?action=dropbox-auth-finish"
        logging.info("redirect_uri is:%s",redirect_uri)
        session = {}
        if csrftok:
            session["dropbox-auth-csrf-token"] = csrftok
        else:
            session["dropbox-auth-csrf-token"] = sessionid+"12345678901234567890"
        obj =  dropbox.client.DropboxOAuth2Flow(os.environ.get('DROPBOX_KEY'), os.environ.get('DROPBOX_SECRET'), redirect_uri, session, "dropbox-auth-csrf-token")
        logging.info("keys = %s"%repr(session))
        return obj

    # URL handler for /dropbox-auth-start
    def dropbox_auth_start(self, sessionid, request):
        authorize_url = self.get_dropbox_auth_flow(sessionid).start(url_state=None)
        logging.info("authorize url  is:%s", authorize_url)        
        self.finish(dict(url=authorize_url))
        #self.redirect(authorize_url)

    # URL handler for /dropbox-auth-finish
    def dropbox_auth_finish(self, sessionid, request):
        import dropbox
        try:
            logging.info(repr(request.arguments))
            req = {}
            for i in list(request.arguments.keys()):
                req[i] = request.arguments[i][0]
            logging.info(repr(req))
            access_token, user_id, url_state = \
                          self.get_dropbox_auth_flow(sessionid, req['state']).finish(req)
            logging.info("user-id=%s" % user_id)
            sessioninfo = {}
            sessioninfo["dbtoken"] = access_token
            sessioninfo["userid"] = user_id
            self.application.mc.set(sessionid, json.dumps(sessioninfo))
            logging.info(self.application.mc.get(sessionid))

            #Storing the token as a cookie temporarily, will shift to memcache
            self.set_cookie('dbToken', access_token)
            self.set_cookie('dbLogin', '1');
            logging.info(self.application.mc.get(sessionid))
            logging.info(repr(sessioninfo))

            # self.finish(dict(token=access_token))
            self.redirect(self.get_cookie('appUrl'))

            #self.finish(dict(token=access_token))
            
        except dropbox.client.DropboxOAuth2Flow.BadRequestException as e:
            logging.info("bad_request")            

        except dropbox.client.DropboxOAuth2Flow.BadStateException as e:
            # Start the auth flow again.
            redirect_to("/dropbox-auth-start")
        except dropbox.client.DropboxOAuth2Flow.CsrfException as e:
            logging.info("csrf exception")            

        except dropbox.client.DropboxOAuth2Flow.NotApprovedException as e:
            logging.info("not approved")            
            self.write("Please approve the app in order to login to Dropbox.")
        except dropbox.client.DropboxOAuth2Flow.ProviderException as e:
            logging.info("Auth error: %s" % (e,))

        
    def get(self):
        try:
            import dropbox
        except ImportError:
            self.set_status(501)
            self.write("Dropbox integration is temporarily unavailable during the Python 3 migration.")
            self.finish()
            return
        action = self.get_argument('action');
        session = self.get_cookie('session');
        logging.info('Action: '+action+', session: '+str(session))
        if action == 'dropbox-auth-start':
            self.dropbox_auth_start(session, self.request)
        elif action == 'dropbox-auth-finish':
            self.dropbox_auth_finish(session, self.request)
        elif action == 'getToken':
            sessioninfo = json.loads(self.application.mc.get(session))
            logging.info(repr(sessioninfo))          
            access_token = sessioninfo['dbtoken']
            self.finish(dict(token=access_token))
        elif action == 'logout':
        	self.clear_cookie('dbLogin')
        	#Using cookie temporarily
        	self.clear_cookie('dbToken')
        	self.finish(dict(status=1))


    #def get(self):
    #    action = self.get_argument('action')
    #    logging.info('Action: '+action)
    #    app_key = os.environ.get('DROPBOX_KEY')
    #    app_secret = os.environ.get('DROPBOX_SECRET')
    #    flow = dropbox.client.DropboxOAuth2FlowNoRedirect(app_key, app_secret)
    #
    #    if action == 'getUrl':
    #        authorize_url = flow.start()
    #        self.finish(dict(url=authorize_url))
    #
    #    elif action == 'getToken':
    #        code = self.get_argument('code')
    #        access_token, user_id = flow.finish(code)
    #        self.finish(dict(token=access_token))

    def post(self):
        try:
            import dropbox
        except ImportError:
            self.set_status(501)
            self.write("Dropbox integration is temporarily unavailable during the Python 3 migration.")
            self.finish()
            return
        action = self.get_argument('action')
        #token = self.get_argument('dbToken')
        token = self.get_cookie('dbToken')
        client = dropbox.client.DropboxClient(token)

        if action == 'upload':
            try:
                data = self.get_argument('string')
                fname = self.get_argument('name')
                response = client.put_file(fname, data)
                print("uploaded: ", response)
                self.finish(dict(data="Done"))
            except dropbox.rest.ErrorResponse as e:
                logging.info(e)
                self.finish(dict(data="Error"))

        elif action == 'listdir':
            try:
                folder_metadata = client.metadata('/')
                print("List of files:", folder_metadata)
                self.finish(folder_metadata)
            except dropbox.rest.ErrorResponse as e:
                logging.info(e)
                self.finish(dict(data="Error"))


        elif action == 'view':
            try:
                f = client.get_file(self.get_argument('fname'))
                fileData = f.read()
                f.close()
                print("downloaded file")
                self.finish(dict(text=fileData))
            except dropbox.rest.ErrorResponse as e:
                logging.info(e)
                self.finish(dict(data="Error"))

        elif action == 'delete':
            try:
                metadata = client.file_delete(self.get_argument('fname'))
                self.finish(dict(data="Done"))
            except dropbox.rest.ErrorResponse as e:
                logging.info(e)
                self.finish(dict(data="Error"))
 
        elif action == 'logout':
            client.disable_access_token()
            self.finish(dict(data="Done"))
 

class InAppHandler(BaseHandler):
        
    def post(self):
        app = self.get_argument('app')
        user = self.get_argument('user')
        print("app: "+app+", user: "+user)
        # self.db.execute("UPDATE UserSheets SET purchased = 1 WHERE user = %s AND fname = %s", user, fname)
        check = self.db.query("SELECT id FROM purchases WHERE app = %s AND user = %s", app, user)
        if check:
        	self.finish(dict(status=-1))
        else:
	        self.db.execute("INSERT INTO purchases (app, user) VALUES (%s, %s)", app, user)
	        # wbook = self.db.query("SELECT purchased FROM UserSheets WHERE user = %s AND fname = %s",user,fname)
	        wbook = self.db.query("SELECT id FROM purchases WHERE app = %s AND user = %s", app, user)
	        if wbook:
	            logging.info("Purchased "+app)
	            self.finish(dict(status=1))
	        else:
	            logging.info("Unable to purchase "+app)
	            self.finish(dict(status=0))

class AmazonWebAppHandler(BaseHandler):
    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,size))
    
    def get(self, **params):
        session = self.get_random_string(6)
        logging.info("session is %s"%session)
        self.set_cookie("session",session)
 
         #This uses the standard get argument structure
         # fname = self.get_argument("app")
        
         #This uses the url parameter structure (amazonwebapp/appname)
        fname = params['param1']
        logging.info(self.request.arguments)

        logging.info(str(params))

        mscpath = "webappTemplates/"
        if params["param2"] == "index.html":
            mscFile = open(mscpath+fname+'/'+fname+'.msc.txt', 'r')
            mscData = mscFile.read()
            with open(mscpath+fname+'/'+fname+'.footers.msc.txt', 'r') as footersFile:    
                footersData = json.load(footersFile)
            
            if footersData == None:
                footerList = ['1', '2', '3', '4', '5', '6', '7']
            else:
                footerList = footersData['footers']
 
            entry = {}
            entry['fname'] = fname
            entry['sheetstr'] = mscData
            entry['sheetmscestr'] = ""
            entry['sheets'] = footerList
            if (self.get_cookie('dbLogin')) == '1':
                entry['dbLogin'] = 1
            else:
                entry['dbLogin'] = 0
            self.set_cookie("appUrl",self.request.uri)
            footersFile.close()
            mscFile.close()
            self.render("amazonwebapp.html", entry=entry)
        else:
            self.set_header("Content-Type","text/html")
            f = os.path.join(self.application.settings["static_path"]+"/runappios43c/",params["param2"])
            self.write(open(f).read())
 




class RestoreInAppHandler(BaseHandler):
    def get(self):
        user = self.get_current_user()
        if user == None:
            self.finish(dict(result="fail"))
            return
        action = self.get_argument('action')
        app = self.get_argument('appname')
        if action == "getInapp":
            dirpath = ["home",user,"securestore","restore"]
            path = ["home",user,"securestore","restore", app]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                print("no directory found, no inapp initialised")
                self.finish(dict(result="no"))
                return
            fileobj = cloud.storage.storage.getFile(path)
            if fileobj == None:
                self.finish(dict(result="no"))
                return
            else:
                filedata = fileobj.data
                logging.info(filedata)
                self.finish(dict(result=filedata))
                return
    def post(self):
        user = self.get_current_user()
        if user == None:
            self.finish(dict(result="fail"))
            return
        action = self.get_argument('action')
        appname = self.get_argument('appname')
        content = self.get_argument('content' ,None)
        print("app is", appname)
        print("action is ", action)
        if action == "inapp":
            print("items are ",content)
            path = ["home",user,"securestore","restore",appname]
            dirpath = ["home",user,"securestore","restore"]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                logging.info("no directory restore")
                cloud.storage.storage.createDir(dirpath)
            if content != None:
                fileobj = cloud.storage.storage.getFile(path)
                if fileobj == None:
                    cloud.storage.storage.createFile(path,content)
                    self.finish(dict(result="ok")) 
                else:
                    cloud.storage.storage.updateFile(path,content)    
                    self.finish(dict(result="ok"))

class FinanceRecordKeeper(BaseHandler):
    def get(self):
        print("Get of Finance Record")
        action = self.get_argument('action')
        user = self.get_current_user()
        if user == None:
            self.finish(dict(result="fail"))
            return
        if action == "transactionList":
            key = self.get_argument('key')
            dirpath = ["home",user,"securestore","finrecord"]
            dirobj = cloud.storage.storage.getFile(dirpath)
            path = ["home",user,"securestore","finrecord", key]
            fileobj = cloud.storage.storage.getFile(path)
            if (fileobj == None):
                logging.info("Error: 2037")
                self.finish(dict(result="fail"))
                return
            self.finish(dict(data=fileobj.data,result="ok"))

    def post(self):
        action = self.get_argument('action')
        user = self.get_current_user()
        if user == None:
            self.finish(dict(result="fail"))
            return
        if action == "save":
            content = self.get_argument('content')
            # print content
            key = self.get_argument('key')
            # print key
            dirpath = ["home",user,"securestore","finrecord"]
            dirobj = cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                # logging.info("no directory restore")
                cloud.storage.storage.createDir(dirpath)
            path = ["home",user,"securestore","finrecord", key]
            fileobj = cloud.storage.storage.getFile(path)
            if fileobj == None:
                cloud.storage.storage.createFile(path,content)
                self.finish(dict(result="ok"))
            else:
                cloud.storage.storage.updateFile(path,content)
                self.finish(dict(result="ok"))

class BusinessRecordKeeper(BaseHandler):
    def get(self):
        print("Get of Business Record")
        self.finish(dict(result="ok"))
    def post(self):
        print("Post of Business Record")
        self.finish(dict(result="ok"))
        

def main():
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    print("DEBUG: Starting server on port", options.port)
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.instance().start()




if __name__ == "__main__":
    main()
