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
from tmodel import Tweet

from demjson import decode as decode_json
from flash import Flash


class IST(datetime.tzinfo):
  def utcoffset(self,dt):
    return datetime.timedelta(hours=5, minutes=30)
  def dst(self,dt):
    return datetime.timedelta(0)
  def tzname(self,dt):
    return "India/Delhi"

class DailyStat(db.Model):
  tweets = db.IntegerProperty(default = 0)
  users = db.IntegerProperty(default = 0)
  fail_tweet = db.IntegerProperty(default = 0)
  created_at = db.DateTimeProperty()

  @staticmethod
  def __key_name(d = None):
    if d == None:
      d = datetime.datetime.now(IST())
    key = "key_%d-%d-%d" % (d.day, d.month,d.year)
    return key

  @classmethod
  def get_by_date(cls, d = None):
    keyname = DailyStat.__key_name(d)
    stat = DailyStat.get_by_key_name(keyname)
    if not stat:
      stat = DailyStat(key_name = keyname)
      stat.put()
    if stat.created_at == None:
      if d:
        stat.created_at = d
      else:
        stat.created_at = datetime.datetime.now(IST())
      stat.put()
    return stat

  def new_user(self):
    self.users += 1
    self.put()

  def new_tweet(self):
    self.tweets += 1
    self.put()

  def failed_tweet(self):
    self.fail_tweet += 1
    self.put()

class Stats(db.Model):
  counter = db.IntegerProperty(default = 130)
  totalUsers = db.IntegerProperty(default = 0)
  recentTweeters = db.StringListProperty(default = ['mayanks', 'romasharma', 'rohitarondekar'])

  @staticmethod
  def __key_name():
    return "key__stats"

  @classmethod
  def singleton(cls):
    try:
      s = Stats.get_by_key_name('key__stats')
      if not s:
        s = Stats(key_name = 'key__stats')
        s.put()
      return s
    except Timeout, e:
      logging.warning("Timedout (singleton): Never mind")
      return Stats()
  
  @classmethod
  def updateCounter(cls,user):
    try:
      stats = Stats.get_by_key_name('key__stats')
      stats.counter += 1
      if user not in stats.recentTweeters:
        stats.recentTweeters.insert(0,user)
        stats.recentTweeters.pop()
      stats.put()

    except Timeout, e:
      logging.warning("Timedout (updateCounter): Never mind")


def is_development():
    logging.debug("server software = %s" % os.environ['SERVER_SOFTWARE'])
    return os.environ['SERVER_SOFTWARE'].startswith('Development')
    
def authorizedAccess(func):
  def wrapper(self, *args, **kw):
    #for kw in self.request.headers.keys():
    #  logging.debug("Request_header[%s] = %s", kw, self.request.headers[kw])
    logging.debug("Request received from %s", self.request.remote_addr)

    if re.match("^64.124.122", self.request.remote_addr) is None:
      logging.error("Looks like someone's trying to fake the sms request")
      for kw in self.request.headers.keys():
        logging.error("Request_header[%s] = %s", kw, self.request.headers[kw])
      logging.error("Request received from %s", self.request.remote_addr)
      self.response.out.write("Your mode of updating the tweet message looks suspicious. We will investigate and update you if required.")
      return
    else:
      func(self,*args,**kw)
  return wrapper

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

# Following will be called as follows 
# http://smstweet.appspot.com?phonecode=%pcode&keyword=%kw&location=%loc&carrier=%car&content=%con&phoneno=%ph&time=%time
# http://smstweet.appspot.com/update?keyword=tweet&phonecode=9220092200&location=Karnataka&carrier=Airtel&content=tweet+this+message+is+geting+treated&msisdn=919845254603&timestamp=1255593493135
class UpdateTwitter(webapp.RequestHandler):

  def updateStatuswithToken(self, tuser, status) :

    updated = True
    client = OAuthClient('twitter', self)

    if len(status) == 0:
      self.response.out.write('Dude, where is the status message to post? Pressed the send button to fast?')
      return

    status_update = { 'status' : status }
    try:
      taskqueue.add(url = '/tasks/post_message', params = { 'phone' : tuser.phonenumber, 'count' : 1, 'status' : status })
      #info = client.post('/statuses/update', 'POST', (200,401), tuser, status=status)  # TODO : this may fail, try three times 
      #if 'error' in info:
      #  logging.error("Submiting failed as credentials were incorrect (user:%s) %s", tuser.user, info['error'])
      #  self.response.out.write('It appears that your OAuth credentials are incorrect. Can you re-register with SMSTweet again? Sorry for the trouble')
      #  keys = info.keys()
      #  for kw in keys: logging.debug("%s : %s", kw, info[kw])
      #  return
      #else:
      #  logging.debug("updated the status for user %s", tuser.user)
      #  updated = True
      #  save_tweet(info)

    except (urlfetch.DownloadError, ValueError), e:
      logging.warning("Update:update could not be fetched. %s " % e)
      msg = "Twitter is having it's fail whale moment, so could not send your message. Can you try again later?"

    else:
      # Since no exception happened, it is safe to assume that the message was posted

      # Get the mentions of the user
      try:
        info = client.get('/statuses/mentions', tuser = tuser,count=1)
        msg = "could not get any message with your mention"
        if info and len(info) > 0:
          if 'text' in info[0]:
            msg = "%s: %s" % (info[0]['user']['screen_name'], info[0]['text'])
      except (urlfetch.DownloadError, ValueError), e:
        logging.warning("Update:mentions could not be fetched. %s " % e)
        msg = "Twitter is having it's fail whale moment, but I guess I managed to post your status."

    if tuser.tweetCount == 0:
      msg = "Welcome to SMSTweet and Congrats on posting your first message. You can sms TWUP to get latest from your timeline. Details at http://smstweet.in/help"
    self.response.out.write(msg)
    return updated

  def registerUser(self, phoneno, user_name, passwd):
    basic_auth = base64.encodestring('%s:%s' % (user_name, passwd))[:-1]
    request_headers = {}
    request_headers['Authorization'] = 'Basic %s' % basic_auth

    try:
      logging.debug("getting account credentials for user %s",user_name)
      resp = urlfetch.fetch('http://twitter.com/account/verify_credentials.json', headers = request_headers, deadline = 10)

      if resp.status_code == 200:
        logging.debug("user name and password are correct for %s", user_name)
        info = decode_json(resp.content)
        tuser = TwitterUser.create_by_phonenumber(phoneno, info['screen_name'], passwd)
        logging.debug("Stored user_name %s as against provided user name %s" % (info['screen_name'], user_name))
        dstat = DailyStat.get_by_date()
        dstat.new_user()

        self.response.out.write("Congratulations !! Your twitter username is registered. Go ahead and send a twitter message by SMSing \"tweet <your twitter status\"")
      else:
        logging.warning("Submiting failed %d and response %s\n", resp.status_code,resp.content) 
        self.response.out.write("Incorrect username/password. Note that both username and password are case sensitive. Better register online at http://www.smstweet.in") 

    except urllib2.URLError, e:
      logging.error("Update: Post to twitter failed\n")
      self.response.out.write("Server error while posting the status. Please try again.\n")
    except urlfetch.DownloadError, e:
      logging.error("Register User verify credentials %s " % e)
      self.response.out.write("Server error while posting the status. Please try again. \n")

  @authorizedAccess
  def get(self):
    phonecode = self.request.get('phonecode')
    keyword = self.request.get('keyword')
    location = self.request.get('location')
    carrier = self.request.get('carrier')
    content = self.request.get('content')
    phoneno = self.request.get('msisdn')
    if phoneno == None or content == None:
      self.response.out.write("Please provide both msisdn and content")
      return

    # Check if the phoneno is registerd and is active
    tuser = TwitterUser.get_by_phonenumber(phoneno)
    r = re.compile('^\s*twe*t\s*',re.I)
    content = r.sub('',content)
    if tuser and tuser.active:
      if len(content) == 0:
        self.response.out.write("Dude !! where is the message to be sent? Hit the send message too fast")
        return

      if re.match("^register", content, re.I): # if content starts with register
        self.response.out.write("Your message cannot start with register as it is a keyword\n")
        return

      updated = False
      status = content[0:139]  # makes sure status is 140 chars long
      updated = self.updateStatuswithToken(tuser, status)

      try:
        if updated:
          dstat = DailyStat.get_by_date()
          dstat.new_tweet()

          tuser.incr_counter(location,carrier)

        else:
          dstat = DailyStat.get_by_date()
          dstat.failed_tweet()

        Stats.updateCounter(tuser.user)
        
      except Timeout, e:
        logging.warning("Timed out logging the stats !! never mind")

    else:
      m = re.match("^\s*(\S+)\s+(\S+)\s+(\S+)", content)
      if m:
        command = m.group(1).lower()
        command = command.lower()
        user_name = m.group(2)
        passwd = m.group(3)

        if command == 'register':
          self.registerUser(phoneno, user_name, passwd)
        else:
          logging.warning("unrecognized command %s\n", command) 
          self.response.out.write("Either you've not registered your phone number at http://www.smstweet.in or we Timed out. Please try again\n") 
      else:
        self.response.out.write("Incorrect syntax. Please sms \"register <username> <passwd>\" Note that password is not being saved")

class GetUpdatesFromTwitter(webapp.RequestHandler):
  @authorizedAccess
  def get(self):
    keyword = self.request.get('keyword')
    content = self.request.get('content')
    phoneno = self.request.get('msisdn')
    if phoneno == None or content == None:
      self.response.out.write("Please provide both msisdn and content")
      return

    # Check if the phoneno is registerd and is active
    tuser = TwitterUser.get_by_phonenumber(phoneno)
    if tuser and tuser.active:
 
      client = OAuthClient('twitter', self)
      count = 0

      words = re.split("\s+",content)
      index = 0
      to_fetch = 1
      user_name = None
      msg = ""
      if len(words) > 1 and words[1] != None:
        try:
          index = int(words[1]) - 1
          logging.debug("User send a request to get %s(%d)\n" % (words[1], index))
          if index < 0 or index >= 100: 
            index = 0
            msg = "Invalid number %d. Only upto 100 allowed." % int(words[1])
          to_fetch = index + 1
        except ValueError, e:
          user_name = words[1].lstrip('@')
          to_fetch = 100
          logging.debug("We are going to get status from %s" % user_name)
      # Get the mentions of the user
      try:
        info = client.get('/statuses/home_timeline',tuser = tuser,count=to_fetch)
        if info and len(info) > 0:
          done = False
          if user_name:
            for item in info:
              r = re.compile(user_name,re.I)
              if r.match(item['user']['screen_name']):
                msg += "@%s: %s" % (item['user']['screen_name'], item['text'])
                done = True
                break
              # endif
            # endfor
            if not done: msg = "No status from %s was found." % user_name
          # endif

          if not done:
            if (len(info) > index) and ('text' in info[index]):
              msg += "@%s: %s" % (info[index]['user']['screen_name'], info[index]['text'])
            else:
              msg += "Could not find %d`th status in your timeline" % index

      except (urlfetch.DownloadError, ValueError), e:
        logging.warning("Twup:timeline could not be fetched. %s " % e)
        msg = "Twitter is having it's fail whale moment, so could you try again at some later time?"
      self.response.out.write(msg[0:159])

    else: # User not registered
      logging.warning("Unregistered user tried to get their updates")
      self.response.out.write("SMSTweet: This command works only for registered users. Register at http://www.smstweet.in")
 

class AboutPage(webapp.RequestHandler):
  def get(self):
    stats = Stats.singleton()

    values = {
      'counter' : intWithCommas(stats.counter),
      'recentTweeters' : stats.recentTweeters
      }
 
    self.response.out.write(template.render('about.html', values))

class HelpPage(webapp.RequestHandler):
  def get(self):
    self.response.out.write(template.render('help.html', None))

class LatestPage(webapp.RequestHandler):
  def get(self):
    self.response.out.write(template.render('latest.html', None))

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
  ('/news', LatestPage),
  ('/update', UpdateTwitter),
  ('/twup', GetUpdatesFromTwitter),
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
