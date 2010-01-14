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
from google.appengine.api.labs import taskqueue

from twitter_oauth_handler import OAuthClient
from twitter_oauth_handler import OAuthHandler
from twitter_oauth_handler import OAuthAccessToken
from tuser import TwitterUser

from demjson import decode as decode_json

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

class Tweet(db.Model):
  screen_name = db.StringProperty()
  profile_image_url = db.StringProperty()
  status = db.TextProperty()
  name = db.StringProperty()
  created_at = db.DateTimeProperty(auto_now_add = True)

def is_development():
    logging.debug("server software = %s" % os.environ['SERVER_SOFTWARE'])
    return os.environ['SERVER_SOFTWARE'].startswith('Development')
    
def save_tweet(info):
  tweet = Tweet(screen_name = info['user']['screen_name'],
                name = info['user']['name'],
                status = info['text'],
                profile_image_url = info['user']['profile_image_url'])
  try:
    tweet.put()
  except Timeout, e:
    logging.warning("Timedout (save_tweet). Never mind")

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

    if len(status) == 0:
      self.response.out.write('Dude, where is the status message to post? Pressed the send button to fast?')
      return

    count = 0
    while count < 3:
      try:
        client.token = OAuthAccessToken.all().filter(
                'specifier =', tuser.user).filter(
                'service =', 'twitter').fetch(1)[0]
        break
      except Timeout, e:
        logging.warning("Timedout(updateStatuswithToken): Trying again")
        count += 1

    if client.token == None:
      logging.error("Could not fetch client token in 3 attempts. Giving up")
      self.response.out.write("SMSTweet is under a heavy load and hence could not tweet your message. Please try again.")
      return

    status_update = { 'status' : status }
    try:
      info = client.post('/statuses/update', 'POST', (200,401), status=status)  # TODO : this may fail, try three times 
      if 'error' in info:
        logging.error("Submiting failed as credentials were incorrect (user:%s) %s", tuser.user, info['error'])
        self.response.out.write('It appears that your OAuth credentials are incorrect. Can you re-register with SMSTweet again? Sorry for the trouble')
        keys = info.keys()
        for kw in keys: logging.debug("%s : %s", kw, info[kw])
        return
      else:
        logging.debug("updated the status for user %s", tuser.user)
        updated = True
        save_tweet(info)

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
        logging.warning("Update:mentions could not be fetched. %s " % e)
        msg = "Twitter is having it's fail whale moment, but I guess I managed to post your status."

    self.response.out.write(msg)
    return updated

  def updateStatuswithPasswd(self, tuser, status):
    updated = False
    request_headers = {}
    request_headers['Authorization'] = 'Basic %s' % tuser.basic_auth
    status_update = "status=%s" % status

    try:
      logging.debug("sending message to twitter, user:%s", tuser.user)
      resp = urlfetch.fetch('http://twitter.com/statuses/update.json',
          status_update,
          urlfetch.POST,
          request_headers, deadline = 10)

      if resp.status_code == 200:
        self.response.out.write("Seems that you've signed in over mobile. You will have more flexibility if you sign up on http://smstweet.in\n")
        info = decode_json(resp.content)
        logging.debug("successfully updated the message with API for %s", tuser.user)
        updated = True
        save_tweet(info)

      else:
        logging.error("Submiting failed %d and response %s\n", resp.status_code,resp.content) 
        self.response.out.write("%d error while updating your status with twitter. Try again later\n" % resp.status_code)

    except urllib2.URLError, e:
      logging.error("Update: Post to twitter failed\n")
      self.response.out.write("Server error while posting the status. Try again later\n")
    except urlfetch.DownloadError,  e:
      logging.error("Update: Post to twitter failed. %s " % e)
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
        tuser = TwitterUser.create_by_phonenumber(phoneno, resp['screen_name'], passwd)
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

    #for kw in self.request.headers.keys():
    #  logging.debug("Request_header[%s] = %s", kw, self.request.headers[kw])
    logging.debug("Request received from %s", self.request.remote_addr)

    if re.match("^Java", self.request.headers['User-Agent'], re.I) is None:
      logging.error("Looks like someone's trying to fake the update request")
      for kw in self.request.headers.keys():
        logging.error("Request_header[%s] = %s", kw, self.request.headers[kw])
      logging.error("Request received from %s", self.request.remote_addr)
      self.response.out.write("Your mode of updating the tweet message looks suspicious. We will investigate and update you if required.")
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
          logging.error("unrecognized command %s\n", command) 
          self.response.out.write("Either you've not registered your phone number at http://www.smstweet.in or we Timed out. Please try again\n") 
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

class LatestPage(webapp.RequestHandler):
  def get(self):
    self.response.out.write(template.render('latest.html', None))

class Statistics(webapp.RequestHandler):
  def get(self):
    tusers = TwitterUser.all().filter(
                'tweetCount >', 0).order('-tweetCount').fetch(20)
    tweets = Tweet.all().order('-created_at').fetch(10)
    values = {
      'highestTweeters' : tusers,
      'tweets' : tweets
      }
    self.response.out.write(template.render('stats.html', values))

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

class FollowNewUser(webapp.RequestHandler):
  def get(self):
    # New user has joined in. Follow him and post a welcome message
    try:
      sms_client = OAuthClient('twitter', self)
      sms_client.token = OAuthAccessToken.all().filter(
                'specifier =', 'smstweetin').filter(
                'service =', 'twitter').fetch(1)[0]

      user = self.request.get('screen_name')
      count = int(self.request.get('count'))
      info = sms_client.post('/friendships/create', 'POST', (200,401,403), screen_name=user)  # TODO : this may fail, try three times 
      # Stop sending the follow status
      #status = "@%s has started using SMSTweet. Welcome %s to the group and tell about us to your friends" % (user, user)
      #info = sms_client.post('/statuses/update', 'POST', (200,401), status=status)  # TODO : this may fail, try three times 
    except (urlfetch.DownloadError, ValueError, Timeout), e:
      logging.warning("SmsTweetin:Friendship/create failed (%d) %s" % (count,e))
      if count > 10:
        logging.error("SmsTweetin:Friendship/create Finally giving up")
      else:
        # Try again
        taskqueue.add(url = '/follow_new_user', params = { 'screen_name' : user, 'count' : count + 1 })

    self.response.out.write("DONE")

  post = get

application = webapp.WSGIApplication([
  ('/', MainPage),
  ('/about', AboutPage),
  ('/help', HelpPage),
  ('/news', LatestPage),
  ('/update', UpdateTwitter),
  ('/stats', Statistics),
  ('/test', Test),
  ('/follow_new_user', FollowNewUser),
  ('/oauth/(.*)/(.*)', OAuthHandler),
], debug=True)


def main():
  wsgiref.handlers.CGIHandler().run(application)


if __name__ == '__main__':
  main()
