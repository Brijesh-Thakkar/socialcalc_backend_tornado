#!/usr/bin/env python
#
# Aspiring Apps Subscription Model
#  
#
#

import tornado.options
import tornado.httpserver
import tornado.ioloop
from . import controllers.main

def main():
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(controllers.main.Application())
    http_server.listen(controllers.main.options.port)
    tornado.ioloop.IOLoop.instance().start()

if __name__ == "__main__":
    main()
