from flask import Flask, session, request, render_template, make_response
from werkzeug.utils import secure_filename
from flask_pymongo import PyMongo
import pylibmc
#from elasticsearch import Elasticsearch
#from elasticsearch_dsl import Search
import requests

import logging, time, json, uuid, os

from config import config
#from pprint import pprint

app = Flask(__name__)

# MongoDB
app.config['MONGO_URI'] = "mongodb://{}:{}@{}/{}".format(
				config['mongo_usr'],
				config['mongo_pwd'],
				config['mongo_ip'],
				config['mongo_db']
			)
mongo = PyMongo(app)

# memcached
mc = pylibmc.Client(["127.0.0.1"], binary=True)
mc.behaviors = {"tcp_nodelay": True, "ketama": True}

# Other Configs
search_route = config["elasticsearch_route"]
profiles_route = config["profiles_route"]

# Setup logging
if __name__ != '__main__':
	gunicorn_logger = logging.getLogger('gunicorn.error')
	app.logger.handlers = gunicorn_logger.handlers
	app.logger.setLevel(gunicorn_logger.level)

@app.route("/reset_search", methods=["POST"])
def reset():
	app.logger.warning("/reset_search called")
	query = {
		"query": {
			"match_all" : {}
		}
	}
	requests.post(url=('http://' + search_route + '/posts/_delete_by_query'), json=query)
	return { "status": "OK" }, 200

@app.route("/search", methods=["POST"])
def search():
	data = request.json
	results = []
	item_collection = mongo.db.items
	app.logger.debug("/search data: {}".format(data))

	# Check memcache
	cached_results = mc.get(json.dumps(data))
	if cached_results:
		app.logger.debug("/search using cached results: {}".format(cached_results))
		for item_id in cached_results:
			mongo_ret = item_collection.find_one({"id": item_id})
			#app.logger.debug(mongo_ret)
			if mongo_ret:
				del mongo_ret['_id']
				results.append(mongo_ret)
				#print(ret[i])
				#results.append(ret[i])
		app.logger.info("/search cached OK")
		return { "status" : "OK", "items": results }, 200

	## Else
	app.logger.debug("/search cache miss")

	# Limit defaults
	limit = 25
	if "limit" in data:
		limit = data["limit"]
	if limit > 100:
		limit = 100

	# Setup search query
	search = []
	filter = []
	#fields = ['username', 'timestamp', 'interest']

	# Time defaults
	timestamp = time.time()
	if "timestamp" in data:
		timestamp = data["timestamp"]
	timestamp = int(round(timestamp*1000))
	filter.append({ "range": {"timestamp": {"lte": timestamp}} })

	# String query
	if "q" in data and data['q']:
		search.append({ "match": {"content": data['q']} })
		#fields.append(content)

	# By username or followed users
	if 'username' in data:
		filter.append({ "term": {"username": data['username']} })
	elif 'user' in data:
		r = requests.post(url=('http://' + profiles_route + '/user/following'),
				json={'username': data['user']})
		r_json = r.json()
		filter.append({ "terms": {"username": r_json['users']} })

	# Exclude replies
	if 'replies' in data and not data['replies']:
		filter.append({ "term": {"isReply": False} })
	# Children of parent only
	elif 'parent' in data:
		filter.append({ "term": {"parent": data['parent']} })
	# has media
	if 'hasMedia' in data and data['hasMedia']:
		filter.append({"exists": {'field': "media"}})
		#fields.append('media')

	query = {
		"_source": ["username", "timestamp", "interest"],
		"query": {
			"bool": {
				"filter": filter
			}
		},
		"sort": [{"interest": "desc"}],
		"size": limit
	}

	if search:
		query['query']['bool']['must'] = search

	# Rank
	if 'rank' in data and data['rank'] == "time":
		app.logger.debug("Sorting by time")
		query['sort'] = [{"timestamp": "desc"}]

	app.logger.debug("/search query: {}".format(query))

	r = requests.get(url=('http://' + search_route + '/posts/_search'), json=query)
	r_json = r.json()
	app.logger.debug(r_json)
	#print(r_json)
	#print(r_json['hits'])
	app.logger.debug(r_json['hits']['hits'])
	app.logger.debug(r_json['hits']['total'])

	cache_ids = []
	for search_result in r_json['hits']['hits']:
		mongo_ret = item_collection.find_one({"id": search_result['_id']})
		#app.logger.debug(mongo_ret)
		if mongo_ret:
			del mongo_ret['_id']
			results.append(mongo_ret)
			cache_ids.append(search_result['_id'])
			#print(ret[i])
			#results.append(ret[i])
	mc.add(json.dumps(data), cache_ids)
	#print(80*'=')
	#pprint(results)
	app.logger.info("/search OK")
	return { "status" : "OK", "items": results }, 200

#	if r_json['hits']['total']['value'] == 0:
#		return { "status" : "error", "error": "No items found" }, 200 #400

