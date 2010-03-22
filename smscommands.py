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
from tmodel import Tweet, DailyStat, Stats, TweetDM, TweetMention

from demjson import decode as decode_json

def is_access_from_smsgupshup(self):
  #for kw in self.request.headers.keys():
  #  logging.debug("Request_header[%s] = %s", kw, self.request.headers[kw])
  logging.debug("Request received from %s", self.request.remote_addr)

  if re.match("^64.124.122", self.request.remote_addr) is None:
    logging.error("Looks like someone's trying to fake the sms request")
    for kw in self.request.headers.keys():
      logging.error("Request_header[%s] = %s", kw, self.request.headers[kw])
    logging.error("Request received from %s", self.request.remote_addr)
    self.response.out.write("Your mode of updating the tweet message looks suspicious. We will investigate and update you if required.")
    return False
  return True

def is_active_user(self):
  self.keyword = self.request.get('keyword')
  self.content = self.request.get('content')
  self.phoneno = self.request.get('msisdn')
  if self.phoneno == None or self.content == None:
    self.response.out.write("Please provide both msisdn and content")
    return False

  # Check if the phoneno is registerd and is active
  self.tuser = TwitterUser.get_by_phonenumber(self.phoneno)
  if self.tuser and self.tuser.active:
    return True 

  logging.warning("Unregistered user tried to get their updates")
  self.response.out.write("SMSTweet: This command works only for registered users. Register at http://www.smstweet.in")
  return False

def authorizedAccess(func):
  def wrapper(self, *args, **kw):
    if is_access_from_smsgupshup(self):
      func(self,*args,**kw)
  return wrapper

def authorizedSignedAccess(func):
  def wrapper(self, *args, **kw):
    if is_access_from_smsgupshup(self) and is_active_user(self):
      func(self,*args,**kw)
  return wrapper

# Following will be called as follows 
# http://smstweet.appspot.com?phonecode=%pcode&keyword=%kw&location=%loc&carrier=%car&content=%con&phoneno=%ph&time=%time
# http://smstweet.appspot.com/update?keyword=tweet&phonecode=9220092200&location=Karnataka&carrier=Airtel&content=tweet+this+message+is+geting+treated&msisdn=919845254603&timestamp=1255593493135
class UpdateTwitter(webapp.RequestHandler):

  def updateStatuswithToken(self, tuser, status) :

    client = OAuthClient('twitter', self)

    if len(status) == 0:
      self.response.out.write('Dude, where is the status message to post? Pressed the send button to fast?')
      return

    if len(status) < 121:
      status = "%s #smstweet" % status
    taskqueue.add(url = '/tasks/post_message', params = { 'phone' : tuser.phonenumber, 'count' : 1, 'status' : status[:140] })

    lastError = tuser.get_last_error()
    if lastError:
      msg = lastError
    elif tuser.tweetCount == 0:
      msg = "Welcome to SMSTweet and Congrats on posting your first message. You can sms TWUP to get latest from your timeline. Details at http://smstweet.in/help"
    elif tuser.reminder and tuser.reminder == 1:
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
    else:
      tuser.reminder = 1
      tuser.put
      msg = "Now you can check your mentions and DM's by sending sms TWME or TWDM, follow @smstweetin for details"

    self.response.out.write(msg)
    return

  def registerUser(self, phoneno, user_name, passwd):
    client = OAuthClient('twitter', self)
    token = client.get_xauth_token(user_name, passwd)
    if token:
      tuser = TwitterUser.create_by_phonenumber(phoneno, token.specifier)
      tuser.accessTokenid = '-1'
      tuser.put()
      dstat = DailyStat.get_by_date()
      dstat.new_user()

      self.response.out.write("Congratulations !! Your twitter username is registered. Go ahead and send a twitter message by SMSing \"twt <your twitter status\"")
    else:
      logging.warning("Failed to get token for user %s with passwd %s\n" % (user_name, passwd )) 
      self.response.out.write("Incorrect username/password. Note that both username and password are case sensitive. Better register online at http://www.smstweet.in") 


  def registerUserOld(self, phoneno, user_name, passwd):
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
    self.content = self.request.get('content')
    self.phoneno = self.request.get('msisdn')
    if self.phoneno == None or self.content == None:
      self.response.out.write("Please provide both msisdn and content")
      return

    # Check if the phoneno is registerd and is active
    tuser = TwitterUser.get_by_phonenumber(self.phoneno)
    r = re.compile('^\s*twe*t\s*',re.I)
    self.content = r.sub('',self.content)
    if tuser and tuser.active:
      if len(self.content) == 0:
        self.response.out.write("Dude !! where is the message to be sent? Hit the send message too fast")
        return

      if re.match("^register", self.content, re.I): # if content starts with register
        self.response.out.write("Your message cannot start with register as it is a keyword\n")
        return

      updated = False
      status = self.content[0:139]  # makes sure status is 140 chars long
      self.updateStatuswithToken(tuser, status)

      try:
        dstat = DailyStat.get_by_date()
        dstat.new_tweet()

        tuser.incr_counter(location,carrier)

        tuser.fetch_mentions_and_dms()
        Stats.updateCounter(tuser.user)
        
      except Timeout, e:
        logging.warning("Timed out logging the stats !! never mind")

    else:
      m = re.match("^\s*(\S+)\s+(\S+)\s+(\S+)", self.content)
      if m:
        command = m.group(1).lower()
        command = command.lower()
        user_name = m.group(2)
        passwd = m.group(3)

        if command == 'register':
          self.registerUser(self.phoneno, user_name, passwd)
        else:
          logging.warning("unrecognized command %s\n", command) 
          self.response.out.write("Either you've not registered your phone number at http://www.smstweet.in or we Timed out. Please try again\n") 
      else:
        self.response.out.write("Incorrect syntax. Please sms \"register <username> <passwd>\" Note that password is not being saved")

class GetUpdatesFromTwitter(webapp.RequestHandler):
  @authorizedSignedAccess
  def get(self):
 
    client = OAuthClient('twitter', self)
    count = 0

    words = re.split("\s+",self.content)
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
      info = client.get('/statuses/home_timeline',tuser = self.tuser,count=to_fetch)
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

    self.tuser.fetch_mentions_and_dms()
    self.response.out.write(msg[0:159])


class GetStatuses(webapp.RequestHandler):

  @authorizedSignedAccess
  def get(self, type):
    logging.debug("Received request to get %s for user %s " % (type, self.tuser.user))
    words = re.split("\s+",self.content)
    index = -1
    msg = ""
    if len(words) > 1 and words[1] != None and words[1] != "":
      try:
        index = int(words[1]) - 1
        logging.debug("User send a request to get %s(%d)\n" % (words[1], index))
        if index < 0 or index >= 100: 
          msg = "Invalid number %d. Only upto 100 allowed." % int(words[1])
      except ValueError, e:
        msg = "Invalid option %s. Only numbers allowed." % words[1]

    if type == 'mention':
      statuses = TweetMention.getLatest(self.tuser.user)
    elif type == 'dm':
      statuses = TweetDM.getLatest(self.tuser.user)
    else:
      self.response.out.write("Unknown option %s" % type)
    total = len(statuses)
    if index == -1:
      for i in range(0,total):
        if statuses[i].read == False:
          index = i
          break
      if index == -1: index = 0
    if index >= total:
      msg += "Sorry, you do not have %d messages yet. Try again" % (index+1)
    else:
      t = statuses[index]
      msg += "(%d of %d)@%s: %s" % (index+1,total,t.sender_screen_name,t.status)
      t.read = True
      try:
        t.put()
      except: pass

    self.tuser.fetch_mentions_and_dms()
    self.response.out.write(msg)
 
class MyDMs(webapp.RequestHandler):
  @authorizedSignedAccess
  def get(self):
    self.response.out.write("SMSTweet: ")

application = webapp.WSGIApplication([
  ('/sms/update', UpdateTwitter),
  ('/sms/twup', GetUpdatesFromTwitter),
  ('/sms/get/(.*)', GetStatuses)
], debug=True)


def main():
  wsgiref.handlers.CGIHandler().run(application)

if __name__ == '__main__':
  main()
