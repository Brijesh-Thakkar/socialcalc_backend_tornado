#!/usr/bin/env python3
#
# Aspiring Apps Web Site
# Ramu Ramamurthy
#
#

#import commands
import logging
import os.path
import re
import tornado.auth
#import tornado.database
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
from tornado.options import define, options
import tornado.escape

import models.user

define("port", default=8888, help="run on the given port", type=int)

class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r"/", tornado.web.RedirectHandler, {"url": "/web/home/index.html"}),
            (r"/web/login", UserLoginHandler),
            (r"/web/logout", UserLogoutHandler),
            (r"/web/register", UserRegisterHandler),
            (r"/web/lostpw", UserLostPasswordHandler),
            (r"/web/home/(.*)", WebHomeHandler),
        ]
        settings = dict(
            app_title="Aspiring Apps",
            template_path=os.path.join(os.path.dirname(__file__), "../templates/mb-aspiring"),
            static_path=os.path.join(os.path.dirname(__file__), "../public"),
            static_url_prefix="/web/static/",
            xsrf_cookies=False,
            cookie_secret=os.environ.get("COOKIE_SECRET", "11oETzKXQAGaYdkL5gEmGeJJFuYh7EQnp2XdTP1o/Vo="),
            login_url="/",
            debug=True,
        )
        tornado.web.Application.__init__(self, handlers, **settings)
        
class BaseHandler(tornado.web.RequestHandler):
    @property
    def db(self):
        return self.application.db

    def get_current_user(self):
        user_json = self.get_secure_cookie("user")
        if not user_json: return None
        return tornado.escape.json_decode(user_json)

class WebHomeHandler(BaseHandler):
    def get(self, slug):
        logging.info("slug is: %r" % slug)
        try:
            self.render(slug)
        except Exception as e:
            logging.error("Error rendering %s: %s" % (slug, str(e)))
            self.redirect("/web/home/log-in.html")

    def post(self, slug):
        logging.info("slug is: %r" % slug)
        try:
            self.render(slug)
        except Exception as e:
            logging.error("Error rendering %s: %s" % (slug, str(e)))
            self.redirect("/web/home/log-in.html")


class UserLoginHandler(BaseHandler):
    def get(self):
        user = self.get_current_user()
        if user is None:
            self.redirect("/web/home/log-in.html")
        else:
            self.redirect("/web/home/index.html")

    def post(self):
        self.redirect("/web/home/log-in.html")


class UserLogoutHandler(BaseHandler):
    def get(self):
        self.clear_cookie("user")
        self.redirect("/web/home/log-in.html")


class UserRegisterHandler(BaseHandler):
    def get(self):
        self.redirect("/web/home/register.html")

    def post(self):
        self.redirect("/web/home/register.html")


class UserLostPasswordHandler(BaseHandler):
    def get(self):
        self.redirect("/web/home/log-in.html")

    def post(self):
        self.redirect("/web/home/log-in.html")
