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

from google.appengine.ext import db
from google.appengine.api import users
from google.appengine.ext import webapp
from google.appengine.api import urlfetch
from google.appengine.ext.webapp import template
from google.appengine.api.datastore_errors import Timeout

from twitter_oauth_handler import OAuthClient
from twitter_oauth_handler import OAuthHandler
from twitter_oauth_handler import OAuthAccessToken

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
    s = Stats.get_by_key_name('key__stats')
    if not s:
      s = Stats(key_name = 'key__stats')
      s.put()
    return s

class TwitterUser(db.Model):
  user = db.StringProperty()
  basic_auth = db.StringProperty()
  phonenumber = db.StringProperty()
  active = db.IntegerProperty(default = 0)
  tweetCount = db.IntegerProperty(default = 0)
  accessTokenid = db.StringProperty()

  @staticmethod
  def __key_name(phnum):
    return "key_%s" % phnum.replace("+","")

  @classmethod
  def get_by_phonenumber(cls, phnum):
    return TwitterUser.get_by_key_name(TwitterUser.__key_name(phnum))

  @classmethod
  def create_by_phonenumber(cls, phnum, user, passwd = None):
    basic_auth = None
    if passwd:
      basic_auth = base64.encodestring('%s:%s' % (user, passwd))[:-1]
    tu = TwitterUser(key_name=TwitterUser.__key_name(phnum), user = user, basic_auth = basic_auth , phonenumber = phnum , active = 1)
    k = tu.put()

    # New user has joined in. Follow him and post a welcome message
    sms_client = OAuthClient('twitter', cls)
    sms_client.token = OAuthAccessToken.all().filter(
                'specifier =', 'smstweetin').filter(
                'service =', 'twitter').fetch(1)[0]

    try:
      info = sms_client.post('/friendships/create', 'POST', (200,401,403), screen_name=user)  # TODO : this may fail, try three times 
      # Stop sending the follow status
      #status = "@%s has started using SMSTweet. Welcome %s to the group and tell about us to your friends" % (user, user)
      #info = sms_client.post('/statuses/update', 'POST', (200,401), status=status)  # TODO : this may fail, try three times 
    except (urlfetch.DownloadError, ValueError), e:
      logging.error("SmsTweetin:Friendship/create failed %s" % e)

    return tu

def is_development():
    logging.debug("server software = %s" % os.environ['SERVER_SOFTWARE'])
    return os.environ['SERVER_SOFTWARE'].startswith('Development')
    
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
        logging.error("Home:Credentials could not be fetched. %s " % e)

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
            tuser.delete()
            tuser = TwitterUser.create_by_phonenumber(phoneno, user_name)
        else:
          tuser = TwitterUser.create_by_phonenumber(phoneno, user_name)
          dstat = DailyStat.get_by_date()
          dstat.new_user()

        if tuser:
          tuser.accessTokenid = client_cookie
          tuser.put()
        else:
          logging.error("Could not save the tuser")
  
        message = "<p>Your phone number %s is registered. Start tweeting using your phone</p><p>Would you like to change this number?</p><form action='/' method='post'> New Phone Number : +91<input type=text name='phoneno' value=''></input><input type='submit' value='Change'></form>" % phoneno
      else:
        # case 2
        # phoneno is not there, so this is get request. display the phone number
        if tuser:
          # case 2.1
          ph = tuser.phonenumber
          message = "<p>You are tweeting currently using phone number %s</p><p>Would you like to change this number?</p><form action='/' method='post'> New Phone Number : +91<input type=text name='phoneno' value=''></input><input type='submit' value='Change'></form>" % ph
        else:
          # case 2.2
          message = "<p>Please provide the phone number using which you would like to tweet</p><form action='/' method='post'> Phone Number : +91<input type=text name='phoneno' value=''></input><input type='submit' value='Add'></form>"

    #if user_name:
    #  message = "Dear %s,<br>%s" % (user_name, message)
    #else:
    #  message = self.response.out.write('<a href="/oauth/twitter/login">Login via Twitter</a>')
 
    stats = Stats.singleton()
    
    if server_error:
      user_name = 'dummy'
      message = '<p>Twitter is having it\'s Fail Whale moment. Please try again after some time. Hopefully things should be back up.</p>'

    values = {
      'user_name' : user_name,
      'message' : message,
      'counter' : stats.counter,
      'recentTweeters' : stats.recentTweeters
      }
    self.response.out.write(template.render('main.html', values))


  post = get

# Following will be called as follows 
# http://smstweet.appspot.com?phonecode=%pcode&keyword=%kw&location=%loc&carrier=%car&content=%con&phoneno=%ph&time=%time
# http://smstweet.appspot.com/update?keyword=tweet&phonecode=9220092200&location=Karnataka&carrier=Airtel&content=tweet+this+message+is+geting+treated&msisdn=919845254603&timestamp=1255593493135
class UpdateTwitter(webapp.RequestHandler):

  def updateStatuswithToken(self, tuser, status) :

    updated = False
    client = OAuthClient('twitter', self)

    client.token = OAuthAccessToken.all().filter(
                'specifier =', tuser.user).filter(
                'service =', 'twitter').fetch(1)[0]

    if len(status) == 0:
      self.response.out.write('Dude, where is the status message to post? Pressed the send button to fast?')
      return

    status_update = { 'status' : status }
    try:
      info = client.post('/statuses/update', 'POST', (200,401), status=status)  # TODO : this may fail, try three times 
      logging.debug("updated the status")
      updated = True

      #keys = info.keys()
      #for kw in keys: logging.debug("%s : %s", kw, info[kw])
    except (urlfetch.DownloadError, ValueError), e:
      logging.error("Update:update could not be fetched. %s " % e)
      msg = "Twitter is having it's fail whale moment, so could not send your message. Can you try again later?"

    else:
      # Since no exception happened, it is safe to assume that the message was posted

      # Get the mentions of the user
      try:
        info = client.get('/statuses/mentions', count=1)
        msg = "could not get any message with your mention"
        if info and len(info) > 0:
          if 'text' in info[0]:
            msg = "%s: %s" % (info[0]['user']['screen_name'], info[0]['text'])
      except (urlfetch.DownloadError, ValueError), e:
        logging.error("Update:mentions could not be fetched. %s " % e)
        msg = "Twitter is having it's fail whale moment, but I guess I managed to post your status."

    self.response.out.write(msg)
    return updated

  def updateStatuswithPasswd(self, tuser, status):
    updated = False
    request_headers = {}
    request_headers['Authorization'] = 'Basic %s' % tuser.basic_auth
    status_update = "status=%s" % status

    try:
      logging.debug("sending message %s to twitter",status_update)
      resp = urlfetch.fetch('http://twitter.com/statuses/update.json',
          status_update,
          urlfetch.POST,
          request_headers, deadline = 10)

      if resp.status_code == 200:
        logging.debug("successfully updated the message %s", resp.content)
        self.response.out.write("Successfully sent the twitter status\n")
        updated = True
      else:
        logging.error("Submiting failed %d and response %s\n", resp.status_code,resp.content) 
        self.response.out.write("%d error while updating your status with twitter. Try again later\n" % resp.status_code)

    except urllib2.URLError, e:
      logging.error("Update: Post to twitter failed\n")
      self.response.out.write("Server error while posting the status. Try again later\n")
    except urlfetch.DownloadError,  e:
      logging.error("Update: Pist to twitter failed. %s " % e)
      msg = "Twitter is having it's fail whale moment. So could you try again later?"


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
        tuser = TwitterUser.create_by_phonenumber(phoneno, user_name, passwd)
        dstat = DailyStat.get_by_date()
        dstat.new_user()

        self.response.out.write("Congratulations !! Your twitter username is registered. Go ahead and send a twitter message by SMSing \"tweet <your twitter status\"")
      else:
        logging.error("Submiting failed %d and response %s\n", resp.status_code,resp.content) 
        self.response.out.write("Incorrect username/password. Note that both username and password are case sensitive. Better register online at http://www.smstweet.in") 

    except urllib2.URLError, e:
      logging.error("Update: Post to twitter failed\n")
      self.response.out.write("Server error while posting the status. Please try again.\n")
    except urlfetch.DownloadError, e:
      logging.error("Register User verify credentials %s " % e)
      self.response.out.write("Server error while posting the status. Please try again. \n")

  def get(self):
    phonecode = self.request.get('phonecode')
    keyword = self.request.get('keyword')
    location = self.request.get('loc')
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
      if tuser.accessTokenid:
        updated = self.updateStatuswithToken(tuser, status)
      else:
        updated = self.updateStatuswithPasswd(tuser, status)

      try:
        if updated:
          dstat = DailyStat.get_by_date()
          dstat.new_tweet()

          tuser.tweetCount += 1
          tuser.put()

        else:
          dstat = DailyStat.get_by_date()
          dstat.failed_tweet()

        stats = Stats.singleton()
        stats.counter += 1
        if tuser.user not in stats.recentTweeters:
          stats.recentTweeters.insert(0,tuser.user)
          stats.recentTweeters.pop()
        stats.put()

      except Timeout, e:
        logging.error("Timed out logging the stats !! never mind")

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
          logging.error("unrecognized command %s\n", command) 
          self.response.out.write("Looks like you've not registered your phone number at http://www.smstweet.in. Sorry can't tweet your message\n") 
      else:
        self.response.out.write("Incorrect syntax. Please sms \"register <username> <passwd>\" Note that password is not being saved")

class AboutPage(webapp.RequestHandler):
  def get(self):
    stats = Stats.singleton()

    values = {
      'counter' : stats.counter,
      'recentTweeters' : stats.recentTweeters
      }
 
    self.response.out.write(template.render('about.html', values))

class HelpPage(webapp.RequestHandler):
  def get(self):
    self.response.out.write(template.render('help.html', None))

class Statistics(webapp.RequestHandler):
  def get(self):
    tusers = TwitterUser.all().filter(
                'tweetCount >', 0).order('-tweetCount').fetch(10)
    values = {
      'highestTweeters' : tusers
      }
    self.response.out.write(template.render('stats.html', values))

application = webapp.WSGIApplication([
  ('/', MainPage),
  ('/about', AboutPage),
  ('/help', HelpPage),
  ('/update', UpdateTwitter),
  ('/stats', Statistics),
  ('/oauth/(.*)/(.*)', OAuthHandler),
], debug=True)


def main():
  wsgiref.handlers.CGIHandler().run(application)


if __name__ == '__main__':
  main()
