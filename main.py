#!/usr/bin/env python
#
# Copyright 2007 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import cgi
import datetime
import wsgiref.handlers
import logging
import base64
import os
import urllib2
import re
import random

from google.appengine.ext import db
from google.appengine.api import users
from google.appengine.ext import webapp
from google.appengine.api import urlfetch
from google.appengine.ext.webapp import template
from google.appengine.api.datastore_errors import Timeout
from google.appengine.api.labs import taskqueue

from twitter_oauth_handler import OAuthClient
from twitter_oauth_handler import OAuthHandler
from twitter_oauth_handler import OAuthAccessToken
from tuser import TwitterUser
from tmodel import Tweet, DailyStat, Stats

from demjson import decode as decode_json
from flash import Flash


def is_development():
    logging.debug("server software = %s" % os.environ['SERVER_SOFTWARE'])
    return os.environ['SERVER_SOFTWARE'].startswith('Development')
    
def intWithCommas(x):
  if type(x) not in [type(0), type(0L)]:
    raise TypeError("Parameter must be an integer.")
  if x < 0:
    return '-' + intWithCommas(-x)
  result = ''
  while x >= 1000:
    x, r = divmod(x, 1000)
    result = ",%03d%s" % (r, result)
  return "%d%s" % (x, result)


class MainPage(webapp.RequestHandler):
  def head(self):
    return

  def get(self):

    if os.environ["HTTP_HOST"].startswith('smstweet.appspot.com'):
      self.redirect("http://www.smstweet.in", permanent=True)
      return

    phoneno = self.request.get('phoneno')
    user_name = None
    message = None
    server_error = False
    client = OAuthClient('twitter', self)

    client_cookie = client.get_cookie()
    
    if client_cookie:
      try:
        info = client.get('/account/verify_credentials', expected_status=(200,401))  # TODO : this may fail, try three times 
        if 'error' in info:
          client.expire_cookie()
        else:
          user_name = info['screen_name']
      except (urlfetch.DownloadError, ValueError), e:
        server_error = True
        logging.warning("Home:Credentials could not be fetched. %s " % e)
      except Timeout, e:
        server_error = True
        logging.warning("Timedout(Home) : Never mind")

    if user_name:
      tuser = None
      tusers = TwitterUser.all().filter('user = ', user_name ).fetch(1)
      if tusers and len(tusers) > 0:
        tuser = tusers[0]

      # case 3
      if phoneno:
        # TODO: Validate (client side) that the phone number does not start with 91 and is 10 digit long

        # TODO: if tuser exists, then delete the existing tuser if phone number don't match
        phoneno = "91%s" % phoneno
        if tuser: 
          if tuser.phonenumber != phoneno:
            oldTweetCount = tuser.tweetCount
            tuser.delete()
            tuser = TwitterUser.create_by_phonenumber(phoneno, user_name)
            tuser.tweetCount = oldTweetCount
            tuser.put()
        else:
          tuser = TwitterUser.create_by_phonenumber(phoneno, user_name)
          dstat = DailyStat.get_by_date()
          dstat.new_user()

        if tuser:
          tuser.accessTokenid = client_cookie
          tuser.put()
        else:
          logging.error("Could not save the tuser")
  
        message = "<p>Your phone number <span class='nos'>%s</span> is registered. Start tweeting using your phone</p><p>Would you like to change this number?</p><form action='/' method='post'><label for='phoneno'>New Phone Number: +91</label><input type=text name='phoneno' value=''></input><input id='formsubmit' type='submit' value='Change'></form>" % phoneno
      else:
        # case 2
        # phoneno is not there, so this is get request. display the phone number
        if tuser:
          # case 2.1
          ph = tuser.phonenumber
          message = "<p>You are tweeting currently using phone number <span class='nos'>%s</span></p><p>Would you like to change this number?</p><form action='/' method='post'><label for='phoneno'>New Phone Number: +91</label><input type=text name='phoneno' value=''></input><input id='formsubmit' type='submit' value='Change'></form>" % ph
        else:
          # case 2.2
          message = "<p>Please provide the phone number using which you would like to tweet</p><form action='/' method='post'> <label for='phoneno'>Phone Number : +91</label><input type=text name='phoneno' value=''></input><input id='formsubmit' type='submit' value='Add'></form>"

    #if user_name:
    #  message = "Dear %s,<br>%s" % (user_name, message)
    #else:
    #  message = self.response.out.write('<a href="/oauth/twitter/login">Login via Twitter</a>')
 
    stats = Stats.singleton()
    
    if server_error:
      user_name = 'dummy'
      message = '<p>Twitter is having it\'s Fail Whale moment. Please try again after some time. Hopefully things should be back up.</p>'

    flash = Flash()

    values = {
      'user_name' : user_name,
      'message' : message,
      'counter' : intWithCommas(stats.counter),
      'recentTweeters' : stats.recentTweeters,
      'flash' : flash
      }
    self.response.out.write(template.render('main.html', values))


  post = get

class AboutPage(webapp.RequestHandler):
  def get(self):
    stats = Stats.singleton()

    values = {
      'counter' : intWithCommas(stats.counter),
      'recentTweeters' : stats.recentTweeters,
      'current' : 'about'
      }
 
    self.response.out.write(template.render('about.html', values))

class HelpPage(webapp.RequestHandler):
  def get(self):
    values = { 'current' : 'usage'}
    self.response.out.write(template.render('help.html', values))

class FaqPage(webapp.RequestHandler):
  def get(self):
    values = { 'current' : 'faq'}
    self.response.out.write(template.render('faq.html', values))

class LatestPage(webapp.RequestHandler):
  def get(self):
    values = { 'current' : 'news'}
    self.response.out.write(template.render('latest.html', values))

class Statistics(webapp.RequestHandler):
  def get(self):
    tusers = TwitterUser.all().filter(
                'tweetCount >', 0).order('-tweetCount').fetch(40)
    tweets = Tweet.all().order('-created_at').fetch(10)
    regexp = re.compile('@(\w+)')
    for t in tweets:
      t.status = regexp.sub(r"<a href='/user/\1'>@\1</a>",t.status)

    values = {
      'highestTweeters' : tusers,
      'tweets' : tweets
      }
    self.response.out.write(template.render('stats.html', values))

class Graph(webapp.RequestHandler):
  def get(self):
    stats = DailyStat.all().order('-created_at').fetch(60)
    values = {
        'stats' : stats,
        'months' : ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
      }
    self.response.out.write(template.render('graph.html', values))


class Test(webapp.RequestHandler):
  def get(self):
    tusers = TwitterUser.all().fetch(1000)
    error_users = []
    for t in tusers:
      if len(t.phonenumber) != 12:
        error_users.append(t)
    values = {
      'tweeters' : error_users,
      }
    self.response.out.write(template.render('test.html', values))


class Tweeter(webapp.RequestHandler):
  def get(self, user=''):
    tuser = tweets = None
    if user == '':
      self.redirect("/stats")
      return

    tusers = TwitterUser.all().filter('user = ', user ).fetch(1)
    if tusers and len(tusers) > 0:
      tuser = tusers[0]
      tweets = Tweet.all().filter('screen_name = ', tuser.user).order('-created_at').fetch(10)
      
      regexp = re.compile('@(\w+)')
      for t in tweets:
        t.status = regexp.sub(r"@<a href='/user/\1'>\1</a>",t.status)
      values = {
        'tuser' : tuser,
        'tweets' : tweets
        }
      self.response.out.write(template.render('tuser.html', values))
    else:
      self.redirect("http://twitter.com/%s" % user)

application = webapp.WSGIApplication([
  ('/', MainPage),
  ('/about', AboutPage),
  ('/help', HelpPage),
  ('/usage', HelpPage),
  ('/faq', FaqPage),
  ('/news', LatestPage),
  ('/stats', Statistics),
  ('/test', Test),
  ('/graph', Graph),
  ('/user/(.*)', Tweeter),
  ('/oauth/(.*)/(.*)', OAuthHandler),
], debug=True)


def main():
  template.register_template_library('filter')
  wsgiref.handlers.CGIHandler().run(application)


if __name__ == '__main__':
  main()
