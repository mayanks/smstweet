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
from google.appengine.api import memcache
from google.appengine.runtime.apiproxy_errors import CapabilityDisabledError

from twitter_oauth_handler import OAuthClient
from twitter_oauth_handler import OAuthHandler
from twitter_oauth_handler import OAuthAccessToken
from tuser import TwitterUser
from tmodel import Tweet, TweetDM, TweetMention

from demjson import decode as decode_json


class FetchStatuses(webapp.RequestHandler):
  def fetch_status(self,type,key_name, url):
    phone = self.request.get('phone')    
    tuser = TwitterUser.get_by_phonenumber(phone)
    if tuser == None:
      logging.warning("Could not fetch tuser based on phone number %s",phone)
      return

    since_id = memcache.get("%s%s" % (key_name,tuser.user))
    if not since_id:
      since_id = -1
      memcache.add("%s%s" % (key_name,tuser.user), since_id)

    client = OAuthClient('twitter', self)
    try:
      info = client.get(url, 'GET', (200,401,403), tuser, since_id=since_id, count = 10)
      if 'error' in info:
        logging.warning("%s Fetch failed for %s because of %s" % (type,tuser.user, info['error']))
      elif len(info) > 0:
        logging.debug("fetched %d %s's for %s" % (len(info), type,tuser.user))
        memcache.replace("%s%s" % (key_name,tuser.user), info[0]['id'])
        if type == 'DM':
          for dm in info: TweetDM.create(dm['sender_screen_name'],tuser.user, dm['text'], dm['created_at'], dm['id'])
        else:
          for dm in info: TweetMention.create(dm['user']['screen_name'],tuser.user, dm['text'], dm['created_at'], dm['id'])
        #endif
      #endif
    except (urlfetch.DownloadError, ValueError), e:
      logging.warning("%s: could not be fetched. %s " % (type,e))

    if type == 'DM':
      oldentries = TweetDM.all().filter('user = ', tuser.user).order('-id').fetch(100,100)
    else:
      oldentries = TweetMention.all().filter('user = ', tuser.user).order('-id').fetch(100,100)
    logging.debug("Found %d old stuff, deleting them" % len(oldentries))
    db.delete(oldentries)
 
  def post(self,type):
    if type == 'dms':
      self.fetch_status('DM','dm_','/direct_messages')
    elif type == 'mentions':
      self.fetch_status('Mentions','mention_','/statuses/mentions')
    else:
      logging.error("Could not understand how to fetch %s" % type)
    self.response.out.write("DONE")
  #endpost

class PostMessage(webapp.RequestHandler):
  def post(self):
    status = self.request.get('status')
    phone = self.request.get('phone')
    count = int(self.request.get('count'))

    tuser = TwitterUser.get_by_phonenumber(phone)
    if tuser == None:
      logging.warning("Could not fetch tuser based on phone number %s",phone)
      return

    client = OAuthClient('twitter', self)
    try:
      info = client.post('/statuses/update', 'POST', (200,401,403), tuser, status=status)
      if 'error' in info:
        logging.error("Submiting failed as credentials were incorrect (user:%s) %s", tuser.user, info['error'])
        tuser.lastError = "Twitter returned '%s' for your last update. You may be over limit or may have to register with SMSTweet again" % info['error']
        tuser.put()
      else:
        logging.debug("updated the status for user %s", tuser.user)
        Tweet.save_tweet(info)

    except (urlfetch.DownloadError, ValueError), e:
      logging.warning("Update:update (%d) could not be fetched. %s " % (count,e))
      if count > 10:
        logging.error("Tried updating the message 10 times. Finally giving up.")
      else:
        # Try again
        taskqueue.add(url = '/tasks/post_message', params = { 'phone' : phone, 'count' : count + 1, 'status' : status })
    except CapabilityDisabledError:
      logging.warning("CapabilityDisabledError: Could not save the tweet but it is ok")

    self.response.out.write("DONE")
  #endpost


class FollowNewUser(webapp.RequestHandler):
  def post(self):
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
        taskqueue.add(url = '/tasks/follow_new_user', params = { 'screen_name' : user, 'count' : count + 1 })

    self.response.out.write("DONE")
  #endpost


application = webapp.WSGIApplication([
  ('/tasks/fetch/(.*)', FetchStatuses),
  ('/tasks/follow_new_user', FollowNewUser),
  ('/tasks/post_message', PostMessage)
], debug=True)


def main():
  wsgiref.handlers.CGIHandler().run(application)


if __name__ == '__main__':
  main()
