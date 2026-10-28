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
import sys
import pymysql
pymysql.install_as_MySQLdb()

# Intercept and patch connect_timeout in PyMySQL connect to support torndb default of 0
orig_connect = pymysql.connect
def patched_connect(*args, **kwargs):
    if kwargs.get("connect_timeout") in (0, None):
        kwargs.pop("connect_timeout", None)
    return orig_connect(*args, **kwargs)
pymysql.connect = patched_connect

# Mock MySQLdb.constants for torndb compatibility
import MySQLdb.constants
import pymysql.constants.FIELD_TYPE
import pymysql.constants.FLAG
sys.modules['MySQLdb.constants'].FIELD_TYPE = sys.modules['pymysql.constants.FIELD_TYPE']
sys.modules['MySQLdb.constants'].FLAG = sys.modules['pymysql.constants.FLAG']

# Patch copy.copy to return lists for converters to satisfy torndb import check
import copy
orig_copy = copy.copy
copy.copy = lambda x: {k: [v] for k, v in orig_copy(x).items()} if isinstance(x, dict) and 253 in x else orig_copy(x)

import torndb

# Restore original copy and set native pymysql converters
copy.copy = orig_copy
torndb.CONVERSIONS = pymysql.converters.conversions
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
import unicodedata

import random
import string
import uuid

#import util.simpledb

from tornado.options import define, options
import util.ystockquote
from util.amazon_ses import AmazonSES,EmailMessage
import util.tickersymbols

channels = {}

define("port", default=8888, help="run on the given port", type=int)
define("mysql_host", default="127.0.0.1:3306", help="database host")
define("mysql_database", default="aspiringinvestments", help="database name")
define("mysql_user", default="ai", help="database user")
define("mysql_password", default="ai", help="database password")


class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r"/", HomeHandler),
            (r"/multisheet", MultiSheetHandler),
            (r"/templates",TemplatesHandler),
            (r"/stock", StockHandler),
            (r"/broadcast", MessageNewHandler),
            (r"/updates", MessageUpdateHandler),
            (r"/sharedsession", SharedSessionHandler),
            (r"/uploadtest", UploadTestHandler),
            (r"/upload", UploadHandler),
            (r"/download",DownloadHandler),
            (r"/downloadfile",DownloadFileHandler),
            (r"/import", ImportHandler),            
            (r"/ticker", TickerHandler),
            (r"/tenyeardata", TenYearDataHandler),
            (r"/save", SaveHandler),
            (r"/embed(.*)", EmbedHandler),            
            (r"/collaborate(.*)", CollaborateHandler),            
            (r"/share", ShareHandler),
            (r"/insert", InsertHandler),            
            (r"/usersheet", UserSheetHandler),
            (r"/tickerjson", TickerJsonHandler)              
        ]
        settings = dict(
            app_title="Aspiring Investments",
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            util_path=os.path.join(os.path.dirname(__file__), "util"),
            #xsrf_cookies=False,
            #cookie_secret=os.environ.get("COOKIE_SECRET"),
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

        self.db = torndb.Connection(
            host=options.mysql_host, database=options.mysql_database,
            user=options.mysql_user, password=options.mysql_password)

class BaseHandler(tornado.web.RequestHandler):
    @property
    def db(self):
        return self.application.db


class HomeHandler(BaseHandler):
    def get(self):
        self.redirect("/save")
        #self.render("sheetshome.html")
        #self.render("testtouch.html")

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

    def wait_for_messages(self, callback, cursor=None):
        if cursor:
            index = 0
            for i in range(len(self.cache)):
                index = len(self.cache) - i - 1
                if self.cache[index]["id"] == cursor: break
            recent = self.cache[index + 1:]
            if recent:
                callback(recent)
                return        
        self.waiters.append(callback)

    def new_messages(self, message):
        logging.info("Sending new message to %r listeners", len(self.waiters))
        for callback in self.waiters:
            try:
                callback(message)
            except:
                logging.error("Error in waiter callback", exc_info=True)
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
    def post(self):
        #create a new channel if id=1 and no channel exists
        cursor = self.get_argument("cursor", None)
        session = self.get_cookie("session")        
        id = self.get_cookie("idinsession")
        #logging.info("long poll id=%s,session=%s"%(id,session))
        channel = channels.get(session,None)
        if channel:
            channel.wait_for_messages(self.on_new_messages,
                                      cursor=cursor)

    def on_new_messages(self, messages):
        # Closed client connection
        if self.request.connection.stream.closed():
            return
        #logging.info(messages)
        #self.write(messages)
        self.finish(dict(messages=messages))


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
class DownloadFileHandler(BaseHandler):
    def post(self):
        logging.info(self.get_argument("type"))
        #logging.info(self.get_argument("content"))
        type = self.get_argument('type')
        #logging.info(self.get_argument('content'))
        if (type != "MSC") and (type != "MSCE") and (type != "HTML") :
            fullfname = "./excelinterop/phpexcel/socialcalc/tmp/tmp"
            inpfile = fullfname+".b"
        
            f = codecs.open(inpfile,encoding='utf-8',mode="w+")
            s = str(self.get_argument('content'))
            #logging.info(s)
            f.write(s)
            f.close()
            outfile = fullfname+"."+suffix[type]
            logging.info(outfile)
            logging.info(inpfile)
            cmdname = "./excelinterop/phpexcel/socialcalc/export.php"
            output = subprocess.getoutput("php %s %s %s %s"%(cmdname, inpfile, outfile, type))
            logging.info(output)
            content = open(outfile).read()
        else:
            content = self.get_argument('content')
        self.set_header("Content-Type", contenttypes[type])
        self.set_header("Content-Disposition", 'attachment;filename='+"tmp."+suffix[type])
        self.set_header("Cache-Control", 'max-age=0') 
        self.write(content)



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

        user = "demo"
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
        session = self.get_cookie("session")

        fname = self.request.files['upload'][0]['filename']
        fcontent = self.request.files['upload'][0]['body']
        if (fname[-3:] != "msc") and (fname[-4:] != "msce") :
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
            
        else:
            wbook = fcontent
    
        sessionfileuploads[fname] = wbook

        self.set_cookie("idinsession",str(1))

        #logging.info(fname)
        #logging.info(wbook)

        entry = {}
        entry['fname'] = fname
        if (fname[-4:] == "msce"):
            entry['sheetmscestr'] = wbook
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
        filename = self.get_argument('filename')
        logging.info("inert filename is "+filename)
        user = "demo"
        wbook = self.db.query("SELECT * FROM UserSheets WHERE user = %s AND fname = %s",user,filename)
        if len(wbook) == 0:
            logging.info("failed finding file");
            self.finish(dict(data="fail",result="fail"))
        else:
            logging.info("found file");            
            self.finish(dict(data=wbook[0].data,result="ok"))                            



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



class SaveHandler(BaseHandler):
    def get(self):
        # display all sheets
        user = "demo"
        entries = self.db.query("SELECT * FROM UserSheets WHERE user = %s",user)
        if not entries:
            #create a default entry
            self.db.execute(
                "INSERT INTO UserSheets (user,fname,data)"
                " VALUES (%s,%s,%s)",
                user, "default", "\n")
            entries = self.db.query("SELECT * FROM UserSheets WHERE user = %s",user)
        for i in entries:
            pass
            #logging.info(i.fname)
            #logging.info("----")
        argument = {}
        #logging.info(argument['ticker'])
        argument['entries'] = entries
        self.render("allusersheets.html", argument=argument)

        
    def post(self):
        fname = self.get_argument('fname')
        logging.info("fname is "+fname)
        sheetstr = self.get_argument("data", None)
        user = "demo"
        if sheetstr != None:
            fname = self.get_argument("fname")
            sheets = self.db.query("SELECT * FROM UserSheets WHERE user = %s AND fname = %s",user,fname)            
            if len(sheets) == 0:
                self.db.execute(
                    "INSERT INTO UserSheets (user,fname,data)"
                    " VALUES (%s,%s,%s)",
                    user, fname, sheetstr)
            else:
                self.db.execute(
                    "UPDATE UserSheets SET data = %s"
                    "WHERE user = %s AND fname = %s", sheetstr, user, fname)
        self.finish(dict(data="Done"))        

        
class UserSheetHandler(BaseHandler):
    def post(self):

        
        user = "demo"
        fname = self.get_argument("pagename")
        logging.info(self.request.arguments)

        #if delete is set, delete the sheet
        isdel = self.get_argument("delete")
        if (isdel == "yes"):
            logging.info("deleting "+fname)
            self.db.execute("DELETE FROM UserSheets WHERE user = %s AND fname = %s",user, fname)
            self.redirect("/save")
            return


        session = self.get_random_string(6)
        logging.info("session is %s"%session)

        self.set_cookie("session",session)
        self.set_cookie("idinsession",str(1))
        
        wbook = self.db.query("SELECT * FROM UserSheets WHERE user = %s AND fname = %s",user,fname)
        #logging.info(wbook)
        entry = {}
        entry['fname'] = fname
        entry['sheetstr'] = wbook[0].data
        entry['sheetmscestr'] = ""
        entry['session'] = session
        channels[session] = MessageMixin(session, "", "")
        self.render("importcollabload.html", entry=entry)
        
    def get_random_string(self,size):
        char_set = string.ascii_uppercase + string.digits
        return ''.join(random.sample(char_set,6))



def main():
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.instance().start()




if __name__ == "__main__":
    main()
