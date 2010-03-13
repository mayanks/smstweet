import logging
import datetime

from google.appengine.ext import db
from google.appengine.ext.db import polymodel

class Tweet(db.Model):
  screen_name = db.StringProperty()
  profile_image_url = db.StringProperty()
  status = db.TextProperty()
  name = db.StringProperty()
  created_at = db.DateTimeProperty(auto_now_add = True)


  @classmethod
  def save_tweet(cls,info):
    tweet = Tweet(screen_name = info['user']['screen_name'],
                name = info['user']['name'],
                status = info['text'],
                profile_image_url = info['user']['profile_image_url'])
    try:
      tweet.put()
    except Timeout, e:
      logging.warning("Timedout (save_tweet). Never mind")

class TweetStatus(polymodel.PolyModel):
  sender_screen_name = db.StringProperty()
  user = db.StringProperty()
  status = db.TextProperty()
  created_at = db.DateTimeProperty(auto_now_add = False)
  id = db.IntegerProperty() 
  read = db.BooleanProperty(default = False)
  
class TweetDM(TweetStatus):
  @classmethod
  def getLatest(cls,user):
    ts = TweetDM.all().filter('user = ',user).order('-id').fetch(100)
    #last_id = -1
    #deleted = False
    #for t in ts:
    #  if t.id == last_id:
    #    logging.debug("found a duplicate entry. deleting it")
    #    db.delete(t)
    #    deleted = True
    #  last_id = t.id
    #if deleted: return TweetDM.getLatest(user)
    return ts

  def getLast(cls,user): 
    ts = TweetDM.all().filter('user = ',user).order('-id').fetch(1)
    if len(ts) > 0: return ts[0]
    return None

  @classmethod
  def create(cls, sender_screen_name, user, status, created_at,id):
    t = TweetDM(sender_screen_name = sender_screen_name,
        user = user,
        status = status,
        created_at = datetime.datetime.strptime(created_at,'%a %b %d %H:%M:%S +0000 %Y'),
        id = id)
    t.put()
    return t


class TweetMention(TweetStatus):
  @classmethod
  def getLatest(cls,user):
    ts = TweetMention.all().filter('user = ',user).order('-id').fetch(100)
    #last_id = -1
    #deleted = False
    #for t in ts:
    #  if t.id == last_id:
    #    logging.debug("found a duplicate entry. deleting it")
    #    db.delete(t)
    #    deleted = True
    #  last_id = t.id
    #if deleted: return TweetMention.getLatest(user)
    return ts

  @classmethod
  def getLast(cls,user): 
    ts = TweetMention.all().filter('user = ',user).order('-id').fetch(1)
    if len(ts) > 0: return ts[0]
    return None

  @classmethod
  def create(cls, sender_screen_name, user, status, created_at,id):
    t = TweetMention(sender_screen_name = sender_screen_name,
        user = user,
        status = status,
        created_at = datetime.datetime.strptime(created_at,'%a %b %d %H:%M:%S +0000 %Y'),
        id = id)
    t.put()
    return t

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


