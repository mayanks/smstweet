import logging
from google.appengine.ext import db

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


