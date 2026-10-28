#!/usr/bin/env python3
#
# Aspiring Investments
#
# 
#
#
# server-repo amazonwebapp.py
import os
import subprocess
import json
import logging
import os.path
import random
import string
import sys
import traceback
import urllib.request, urllib.parse, urllib.error
import uuid

import memcache
import tornado.auth
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
from tornado.options import define, options

import amazon_cloud.authenticate.user
import amazon_cloud.storage.storage

from util.amazon_ses import AmazonSES,EmailMessage

#import util.ystockquote
#import util.simpledb
#import util.tickersymbols

channels = {}

define("port", default=8080, help="run on the given port", type=int)

class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r"/runasemailer",RunAsEmailHandler),
            (r"/webapps/(?P<param1>[^\/]+)/dropbox", DropBoxHandler),
            (r"/webapps/(?P<param1>[^\/]+)/(?P<paramCode>[^\/]+)/(?P<param2>[^\/]+)", AmazonWebAppHandler),
            (r"/webapp", WebAppHandler),
            (r"/auth",AuthenticationHandler),
            (r"/webapps/(.*)",GoogleVerificationHandler),
            (r"/webapps",LandingHandler)
        ]
        settings = dict(
            app_title="Aspiring Investments",
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            util_path=os.path.join(os.path.dirname(__file__), "util"),
            cloud_path=os.path.join(os.path.dirname(__file__), "amazon_cloud"),
            xsrf_cookies=False,
            cookie_secret=os.environ.get("COOKIE_SECRET"),
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
        self.mc = memcache.Client(['127.0.0.1'], debug=0)

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


class GoogleVerificationHandler(BaseHandler):
    def get(self,slug):
        print("slug is", slug)
        self.render(slug)

class LandingHandler(BaseHandler):
    def get(self):
        self.render("landing-page.html")
                        
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
        if amazon_cloud.authenticate.user.authenticate_user(user,password):
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
        if amazon_cloud.authenticate.user.user_exists(user):
            # user already exists
            argument = {}
            argument['user'] = None            
            argument['reguser'] = user
            self.render("userregister-exists.html", argument=argument)
            return
        amazon_cloud.authenticate.user.create_user(user,password)
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
        if not amazon_cloud.authenticate.user.user_exists(user):
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
        amazon_cloud.authenticate.user.set_user_dongle(user,dongle)
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
        if (not (dongle == None)) and (dongle == amazon_cloud.authenticate.user.get_user_dongle(user)):
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
        if not amazon_cloud.authenticate.user.user_exists(user):
            # user does not exist
            argument = {}
            argument['user'] = None            
            argument['reguser'] = user
            self.render("lostpassword-baduser.html",  argument=argument)            
            return
        amazon_cloud.authenticate.user.update_password(user,password)
        argument = {}
        argument['user'] = None
        argument['reguser'] = user
        self.render("pwreset-ok.html", argument=argument)
        pass


class RunAsEmailHandler(BaseHandler):
    def post(self):
        user = self.get_current_user()
        if user == None:
            defaultSubject = self.get_argument("appname")
        else:
            defaultSubject = user + " has shared "+self.get_argument("appname")
        to = self.get_argument("to")
        content = self.get_argument("data")
        message = EmailMessage()

        message.subject = self.get_argument("subject", defaultSubject)

        message.bodyHtml = '<div><p>'+self.get_argument("text", "")+'</p></div>'+content

        logging.info(to)
        #logging.info(subject)
        #logging.info(content)                
        self.application.amazonSes.sendEmail(self.application.fromemail, to, message)
        self.finish(dict(data=to))        


class AuthenticationHandler(BaseHandler):
    def post(self):
        action = self.get_argument('action')

        # Login action here
        if action == "login":
            print("Login action started")
            email = self.get_argument('email')
            password = self.get_argument('pwd')
            if amazon_cloud.authenticate.user.authenticate_user(email, password):
                print("authenticate succeeded")
                self.set_current_user(email)
                self.finish(dict(data="success", result="ok"))
            elif amazon_cloud.authenticate.user.user_exists(email) is False:
                self.finish(dict(data="usererror", result="fail"))
            else:
                print("authenticate failed")
                self.finish(dict(data="authfail", result="fail"))

        # Register action here
        if action == "register":
            print("Register action started")
            user = self.get_argument('email')
            password = self.get_argument('pwd')
            if amazon_cloud.authenticate.user.user_exists(user):
                self.finish(dict(data="userexists", result="fail"))
                return
            user_directory = "users"
            user_directory_path = ["home", "users"]
            if amazon_cloud.storage.storage.getFile(user_directory) is None:
                print("Creating parent directory")
                amazon_cloud.storage.storage.createDir(user_directory_path)
            amazon_cloud.authenticate.user.create_user(user, password)
            self.set_current_user(user)
            path = ["home", user]
            directory_obj = amazon_cloud.storage.storage.getFile(path)
            if (not directory_obj) or (len(directory_obj.files) == 0):
                print("Creating directory for user ", user)
                amazon_cloud.storage.storage.createDir(path)
                self.finish(dict(data="success", result="ok"))

        # Logout action here
        if action == "logout":
            self.clear_cookie("user")
            self.finish(dict(result="ok"))

class WebAppHandler(BaseHandler):
    def post(self):
        action = self.get_argument('action')
        logging.info("action is " + action)
        user = self.get_current_user()
        if user == None:
            # this cannot happen
            self.finish(dict(data="usererror", result="fail"))
            return
        logging.info("user is "+user)        

        if action == "savefile":
            appname = self.get_argument('appname')
            fname = self.get_argument('fname')        
            logging.info("appname is "+appname)
            logging.info("fname is "+fname)        
            sheetstr = self.get_argument("data", None)
            path = ["home",user,"appstore",appname,fname]
            #if directory does not exist, create it
            dirpath = ["home",user,"appstore",appname]
            dirobj = amazon_cloud.storage.storage.getFile(dirpath)
            if (not dirobj) or (len(dirobj.files) == 0):
                logging.info("no directory")
                amazon_cloud.storage.storage.createDir(dirpath)
            #dir is now created
            if sheetstr != None:
                fileobj = amazon_cloud.storage.storage.getFile(path)
                if fileobj == None:
                    amazon_cloud.storage.storage.createFile(path,sheetstr)
                else:
                    amazon_cloud.storage.storage.updateFile(path,sheetstr)
                    self.finish(dict(result="ok"))
        if action == "getfile":
            appname = self.get_argument('appname')
            fname = self.get_argument('fname')        
            logging.info("appname is "+appname)
            logging.info("fname is "+fname)                    
            path = ["home",user,"appstore",appname,fname]
            fileobj = amazon_cloud.storage.storage.getFile(path)
            if (fileobj == None):
                logging.info("File not found "+fname)
                self.finish(dict(result="fail"))
                return
            self.finish(dict(data=fileobj.data,result="ok"))


        if action == "delete-file":
            appname = self.get_argument('appname')
            filename = self.get_argument('filename')
            path = ["home", user, appname, "files", filename]
            amazon_cloud.storage.storage.deleteFile(path)
            self.finish(dict(result="ok"))


        if action == "listdir":
            appname = self.get_argument('appname')
            logging.info("appname is " + appname)
            path = ["home", user, appname, "files"]
            dirobj = amazon_cloud.storage.storage.getFile(path)
            if (not dirobj) or (len(dirobj.files) == 0):
                amazon_cloud.storage.storage.createDir(path)
                dirobj = amazon_cloud.storage.storage.getFile(path)
            entries = [i.fname for i in dirobj.files]
            self.finish(dict(data=entries, result="ok"))

        # Multiple files save
        if action == "save-multiple":
            data = self.get_argument('content')
            appname = self.get_argument('appname')
            # print data
            data = json.loads(data)
            for filename in data:
                content = data[filename]
                print(filename," ,",content)
                path = ["home", user, appname, "files", filename]
                directory_path = ["home", user, appname, "files"]
                directory_obj = amazon_cloud.storage.storage.getFile(directory_path)
                if (not directory_obj) or (len(directory_obj.files) == 0):
                    print("Creating directory for app ", appname)
                    amazon_cloud.storage.storage.createDir(directory_path)
                if content is not None:
                    file_obj = amazon_cloud.storage.storage.getFile(path)
                    if file_obj is None:
                        amazon_cloud.storage.storage.createFile(path, content)
                    else:
                        amazon_cloud.storage.storage.updateFile(path, content)
            self.finish(dict(result="ok"))

        if action == "get-data":
            files = json.loads(self.get_argument('content'))
            appname = self.get_argument('appname')
            data = {}
            for filename in files:
                path = ["home", user, appname, "files", filename]
                file_obj = amazon_cloud.storage.storage.getFile(path)
                data[filename] = file_obj.data
            self.finish(dict(result="ok",data=data))

class WebappSession:

    def __init__(self, sessionid, mc):
        self.id = sessionid
        self.mc = mc
        data = self.mc.get(sessionid)
        if data:
            self.data = json.loads(data)
        else:
            self.data = {"id": sessionid}
            self.mc.set(self.id, json.dumps(self.data))
        logging.info("sessiondata: %s", repr(self.data))        

    def set(self, key, value):
        self.data[key] = value
        self.mc.set(self.id, json.dumps(self.data))

    def pop(self, key):
        if key in self.data:
            self.data.pop(key)
        self.mc.set(self.id, json.dumps(self.data))        

    def get(self, key):
        if key in self.data:
            return self.data[key]
        else:
            return None

    def delete(self):
        self.mc.delete(self.id)        


class DropBoxHandler(BaseHandler):
    # Test code for automatic redirection from auth URL

    def get_dropbox_auth_flow(self, sessionid, csrftok=None):
        import dropbox
        sessobj = WebappSession(sessionid, self.application.mc)
        fname = sessobj.get('appName')
        redirect_uri = "https://%s"%(self.request.host)+"/webapps/"+fname+"/dropbox?action=dropbox-auth-finish"

        logging.info("redirect_uri is:%s",redirect_uri)
        session = {}
        if csrftok:
            session["dropbox-auth-csrf-token"] = csrftok
        else:
            session["dropbox-auth-csrf-token"] = sessionid

        mscpath = "webappTemplates/"

        with open(mscpath+fname+'/'+fname+'.config.txt', 'r') as configFile:    
            configData = json.load(configFile)
        dbKey = configData["dropbox"]["key"]
        dbSecret = configData["dropbox"]["secret"]


        obj =  dropbox.client.DropboxOAuth2Flow(dbKey, dbSecret, redirect_uri, session, "dropbox-auth-csrf-token")
        logging.info("keys = %s"%repr(sessobj))
        return obj

    # URL handler for /dropbox-auth-start
    def dropbox_auth_start(self, sessionid, request):
        authorize_url = self.get_dropbox_auth_flow(sessionid).start(url_state=None)
        logging.info("authorize url  is:%s", authorize_url)        
        #self.redirect(authorize_url)
        self.finish(dict(url=authorize_url))

    # URL handler for /dropbox-auth-finish
    def dropbox_auth_finish(self, sessionid, request):
        import dropbox
        sessobj = WebappSession(sessionid, self.application.mc)
        try:
            logging.info(repr(request.arguments))
            req = {}
            for i in list(request.arguments.keys()):
                req[i] = request.arguments[i][0]
            logging.info(repr(req))
            access_token, user_id, url_state = \
                          self.get_dropbox_auth_flow(sessionid, req['state']).finish(req)
            logging.info("user-id=%s" % user_id)
            sessobj.set("dbToken", access_token)
            sessobj.set("userid", user_id)
            sessobj.set("dbLogin", "1")


            # self.finish(dict(token=access_token))
            self.redirect(sessobj.get('appUrl'))

            #self.finish(dict(token=access_token))
            #self.write("<H2>Signed in to dropbox</H2>")

            
        except:
            logging.info("bad_request")
            appUrl = sessobj.get('appUrl')
            # clear the session
            sessobj.delete()
            self.redirect(appUrl)
            

        
    def get(self, **params):
        try:
            import dropbox
        except ImportError:
            self.set_status(501)
            self.write("Dropbox integration is temporarily unavailable during the Python 3 migration.")
            self.finish()
            return
        action = self.get_argument('action');
        #sessionid = str(self.get_argument('sessionid'))
        sessionid = self.get_cookie('session')
        sessobj = WebappSession(sessionid, self.application.mc)

        logging.info('Action: '+action+', session: '+str(sessionid))
        if action == 'dropbox-auth-start':
            sessobj.pop('dbLogin')
            sessobj.pop('dbToken')
            self.dropbox_auth_start(sessionid, self.request)
        elif action == 'dropbox-auth-finish':
            self.dropbox_auth_finish(sessionid, self.request)
        elif action == 'getLogin':
            login = sessobj.get('dbLogin')
            self.finish(dict(login=login))
        elif action == 'logout':
            token = sessobj.get('dbToken')
            sessobj.pop('dbLogin')
            sessobj.pop('dbToken')
            if token:
                client = dropbox.client.DropboxClient(token)
                client.disable_access_token()
            self.finish(dict(status=1))

    def post(self, **params):
        try:
            import dropbox
        except ImportError:
            self.set_status(501)
            self.write("Dropbox integration is temporarily unavailable during the Python 3 migration.")
            self.finish()
            return
        action = self.get_argument('action')
        #sessionid = str(self.get_argument('sessionid'))
        sessionid = self.get_cookie('session')        
        sessobj = WebappSession(sessionid, self.application.mc)
        logging.info('Action: '+action+', session: '+str(sessionid))
        token = sessobj.get('dbToken')
        if (not token) or (token == ""):
            self.finish(dict(data="Please login to dropbox"))            
            return
        client = dropbox.client.DropboxClient(token)

        if action == 'upload':
            try:
                data = self.get_argument('string')
                fname = self.get_argument('name')
                response = client.put_file(fname, data, True)
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
            sessobj.pop('dbLogin')
            sessobj.pop('dbToken')
            # clear the seesion id also ?
            self.finish(dict(data="Done"))
 


class AmazonWebAppHandler(BaseHandler):
    def get_random_string(self,size):
        count = 0
        while count < 100:
            count = count + 1
            char_set = string.ascii_uppercase + string.digits
            idx = ''.join(random.sample(char_set,size))
            if not self.application.mc.get(idx):
                return idx
            else:
                logging.info("id exists, try another %s"%idx)                
    
    def get(self, **params):
        # first check if in session already
        sessionid = self.get_cookie('session')
        fname = params['param1']
        sessobj = None
        cookiesessionpath = "/webapps/"+fname 
        if not sessionid:
            sessionid = self.get_random_string(16)
            logging.info("session doesnt exist, creating one %s"%sessionid)
            self.set_cookie("session",sessionid, path=cookiesessionpath)
            sessobj = WebappSession(sessionid, self.application.mc)
            
        else:
            logging.info("session cookie exists %s"%sessionid)
            resetSession = False
            if self.application.mc.get(sessionid):
                sessobj = WebappSession(sessionid, self.application.mc)
                if sessobj.get("appName") != fname:
                    # A wrong cookie is presented !!!
                    logging.info("!!!!!!!ERROR!!!!!!!!! - wrong cookie, appname"+fname)
                    resetSession = True
            else:
                resetSession = True
            if resetSession:
                sessionid = self.get_random_string(16)
                logging.info("session doesnt exist, creating one %s"%sessionid)
                self.set_cookie("session",sessionid, path=cookiesessionpath)
                sessobj = WebappSession(sessionid, self.application.mc)
                
        logging.info(self.request.arguments)

        logging.info(str(params))

        mscpath = "webappTemplates/"
        if params["param2"] == "index.html":
            mscFile = open(mscpath+fname+'/'+fname+'.msc.txt', 'r')
            mscData = mscFile.read()
            with open(mscpath+fname+'/'+fname+'.config.txt', 'r') as configFile:    
                configData = json.load(configFile)
            
            if configData == None:
                footerList = ['1', '2', '3', '4', '5', '6', '7']
            else:
                footerList = configData['footers']

            randomCode = configData['code']

            logging.info(randomCode)
            if params["paramCode"] != randomCode:
                self.write("Invalid code")
                return
 
            entry = {}
            entry['fname'] = fname
            entry['sheetstr'] = mscData
            entry['sheetmscestr'] = ""
            entry['appjsfiles'] = ""
            entry['appstylefiles'] = ""
            entry['sheets'] = footerList
            entry['sessionid'] = sessobj.get('id')
            if sessobj.get('dbLogin'):
                entry['dbLogin'] = 1
            else:
                entry['dbLogin'] = 0
            sessobj.set("appUrl",self.request.uri)
            configFile.close()
            mscFile.close()
            sessobj.set("appName", fname)
            self.render("amazonwebapp.html", entry=entry)
        elif params["param2"] == "appsplash.png" :
            logging.info("loading appsplash")
            # this could be made faster by serving it from nginx
            self.set_header("Content-Type","image/png")
            f = open(mscpath+fname+'/appsplash.png', 'rb')
            self.write(f.read())
        else:
            self.set_header("Content-Type","text/html")
            f = os.path.join(self.application.settings["static_path"]+"/runappios43c/",params["param2"])
            self.write(open(f).read())


def main():
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.instance().start()




if __name__ == "__main__":
    main()
