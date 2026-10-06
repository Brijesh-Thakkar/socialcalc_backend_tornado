#!/usr/bin/env python3
#
# Aspiring Apps Web Site
# Ramu Ramamurthy
#
#

import tornado.options
import tornado.httpserver
import tornado.ioloop
from .controllers import main as controllers_main

def main():
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(controllers_main.Application())
    http_server.listen(controllers_main.options.port)
    tornado.ioloop.IOLoop.current().start()

if __name__ == "__main__":
    main()
