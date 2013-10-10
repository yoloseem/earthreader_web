from collections import deque
import hashlib
import os.path
try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

from libearth.compat import binary
from libearth.feed import Feed
from libearth.feedlist import (Feed as FeedOutline,
                               FeedCategory as CategoryOutline, FeedList)
from libearth.parser.autodiscovery import autodiscovery, FeedUrlNotFoundError
from libearth.parser.heuristic import get_format
from libearth.schema import read, write

from flask import Flask, jsonify, render_template, request, url_for


app = Flask(__name__)


app.config.update(dict(
    REPOSITORY='repo/',
    OPML='earthreader.opml'
))


def is_exist_feedlist():
    REPOSITORY = app.config['REPOSITORY']
    OPML = app.config['OPML']
    if not os.path.isfile(REPOSITORY + OPML):
        return False
    return True


def get_feedlist():
    REPOSITORY = app.config['REPOSITORY']
    OPML = app.config['OPML']
    if not os.path.isfile(REPOSITORY + OPML):
        if not os.path.isdir(REPOSITORY):
            os.mkdir(REPOSITORY)
        feed_list = FeedList()
        feed_list.save_file(REPOSITORY + OPML)

    feed_list = FeedList(REPOSITORY + OPML)

    return feed_list


def get_hash(name):
    return hashlib.sha1(binary(name)).hexdigest()


@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


def get_all_feeds(category, parents=[]):
    feeds = []
    feed_path = '/'.join(parents)
    for obj in category:
        if isinstance(obj, FeedOutline):
            feeds.append({
                'title': obj.title,
                'feed_url': url_for(
                    'category_feed_entries',
                    category_id=feed_path,
                    feed_id=get_hash(obj.xml_url),
                    _external=True)
            })
        else:
            feeds.append({
                'title': obj.title,
                'feed_url': url_for(
                    'category_all_entries',
                    category_id=feed_path + '/' + obj.title,
                    _external=True),
                'feeds': get_all_feeds(obj)
            })
    return feeds


def check_path_valid(category_id, return_category_parent=False):
    if return_category_parent:
        category_list = category_id.split('/')
        target = category_list.pop()
        categories = deque(category_list)
    else:
        target = None
        categories = deque(category_id.split('/'))
    feed_list = get_feedlist()
    cursor = feed_list
    while categories:
        is_searched = False
        looking_for = categories.popleft()
        for category in cursor:
            if category.text == looking_for:
                is_searched = True
                cursor = category
                break
        if not is_searched:
            return None, None, None
    return feed_list, cursor, target


def find_feed_in_opml(feed_id, category, parent_categories=[], result=[]):
    categories = []
    if parent_categories:
        feed_path = '/'.join(parent_categories)
    else:
        feed_path = '/'
    for child in category:
        if isinstance(child, FeedOutline):
            current_feed_id = hashlib.sha1(binary(child.xml_url)).hexdigest()
            if current_feed_id == feed_id:
                result.append(feed_path)
        elif isinstance(child, CategoryOutline):
            categories.append(child)
    for category in categories:
        find_feed_in_opml(
            feed_id,
            category,
            parent_categories.append(category.title)
            if parent_categories else [category.title],
            result
        )
    return result


def add_feed(url, feed_list=None, cursor=None):
    REPOSITORY = app.config['REPOSITORY']
    if not feed_list:
        feed_list = get_feedlist()
        cursor = feed_list
    try:
        url = request.form['url']
        f = urllib2.urlopen(url)
        document = f.read()
    except ValueError:
        r = jsonify(
            error='unreachable-url',
            message='Cannot connect to given url'
        )
        r.status_code = 400
        return r
    try:
        feed_url = autodiscovery(document, url)
    except FeedUrlNotFoundError:
        r = jsonify(
            error='unreachable-feed-url',
            message='Cannot find feed url'
        )
        r.status_code = 400
        return r
    if not feed_url == url:
        f.close()
        f = urllib2.urlopen(feed_url)
        xml = f.read()
    else:
        xml = document
    format = get_format(xml)
    result = format(xml, feed_url)
    feed = result[0]
    outline = FeedOutline('atom', feed.title.value, feed_url)
    for link in feed.links:
            if link.relation == 'alternate' and \
                    link.mimetype == 'text/html':
                outline.blog_url = link.uri
    cursor.append(outline)
    feed_list.save_file()
    file_name = hashlib.sha1(binary(feed_url)).hexdigest() + '.xml'
    with open(os.path.join(REPOSITORY, file_name), 'w') as f:
        for chunk in write(feed, indent='    ', canonical_order=True):
            f.write(chunk)


def add_category(title, feed_list=None, cursor=None):
    REPOSITORY = app.config['REPOSITORY']
    if not feed_list:
        feed_list = get_feedlist()
        cursor = feed_list
    title = request.form['title']
    outline = CategoryOutline(title)
    cursor.append(outline)
    feed_list.save_file()


def delete_feed(feed_id, feed_list=None, cursor=None):
    REPOSITORY = app.config['REPOSITORY']
    if not feed_list:
        if not is_exist_feedlist():
            r = jsonify(
                error='opml-not-found',
                message='Cannot open OPML'
            )
            r.status_code = 400
            return r
        else:
            feed_list = get_feedlist()
        cursor = feed_list
    target = None
    for feed in cursor:
        if isinstance(feed, FeedOutline):
            if feed_id == hashlib.sha1(binary(feed.xml_url)).hexdigest():
                target = feed
    if target:
        cursor.remove(target)
    else:
        r = jsonify(
            error='feed-not-found-in-path',
            message='Given feed does not exists in the path'
        )
        r.status_code = 400
        return r
    feed_list.save_file()
    if not find_feed_in_opml(feed_id, feed_list):
        os.remove(REPOSITORY + feed_id + '.xml')


@app.route('/feeds/', methods=['GET'])
def feeds():
    if not is_exist_feedlist():
        r = jsonify(
            error='opml-not-found',
            message='Cannot open OPML'
        )
        r.status_code = 400
        return r
    feed_list = get_feedlist()
    feeds = get_all_feeds(feed_list)
    return jsonify(feeds=feeds)


@app.route('/<path:category_id>/feeds/')
def category_feeds(category_id):
    feed_list, cursor, _ = check_path_valid(category_id)
    if not isinstance(cursor, CategoryOutline):
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    feeds = get_all_feeds(cursor, [category_id])
    return jsonify(feeds=feeds)


POST_FEED = 'feed'
POST_CATEGORY = 'category'


@app.route('/feeds/', methods=['POST'])
def post_feed():
    if request.form['type'] == POST_FEED:
        url = request.form['url']
        add_feed(url)
        return feeds()
    elif request.form['type'] == POST_CATEGORY:
        title = request.form['title']
        add_category(title)
        return feeds()


@app.route('/<path:category_id>/feeds/', methods=['POST'])
def post_feed_in_category(category_id):
    feed_list, cursor, _ = check_path_valid(category_id)
    if not cursor:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    if request.form['type'] == POST_FEED:
        url = request.form['url']
        add_feed(url, feed_list, cursor)
        return category_feeds(category_id)
    elif request.form['type'] == POST_CATEGORY:
        title = request.form['title']
        add_category(title, feed_list, cursor)
        return category_feeds(category_id)


@app.route('/feeds/<feed_id>/', methods=['DELETE'])
def delete_feed_in_root(feed_id):
    r = delete_feed(feed_id)
    if r:
        return r
    return feeds()


@app.route('/<path:category_id>/', methods=['DELETE'])
def delete_category(category_id):
    feed_list, cursor, target = check_path_valid(category_id, True)
    if not cursor:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    for child in cursor:
        if isinstance(child, CategoryOutline):
            if child.text == target:
                cursor.remove(child)
    feed_list.save_file()
    index = category_id.rfind('/')
    if index == -1:
        return feeds()
    else:
        return category_feeds(category_id[:index])


@app.route('/<path:category_id>/feeds/<feed_id>/', methods=['DELETE'])
def delete_feed_in_category(category_id, feed_id):
    feed_list, cursor, _ = check_path_valid(category_id)
    if not cursor:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    r = delete_feed(feed_id, feed_list, cursor)
    if r:
        return r
    return category_feeds(category_id)


@app.route('/feeds/<feed_id>/entries/')
def feed_entries(feed_id):
    REPOSITORY = app.config['REPOSITORY']
    try:
        with open(os.path.join(REPOSITORY, feed_id + '.xml')) as f:
            feed = read(Feed, f)
            entries = []
            for entry in feed.entries:
                entries.append({
                    'title': entry.title,
                    'entry_url': url_for(
                        'feed_entry',
                        feed_id=feed_id,
                        entry_id=get_hash(entry.id),
                        _external=True
                    ),
                    'updated': entry.published_at
                })
        return jsonify(
            title=feed.title,
            entries=entries
        )
    except IOError:
        r = jsonify(
            error='feed-not-found',
            message='Given feed does not exist'
        )
        r.status_code = 404
        return r


@app.route('/<path:category_id>/entries/')
def category_all_entries(category_id):
    REPOSITORY = app.config['REPOSITORY']
    lst, cursor, target = check_path_valid(category_id)

    if not cursor:
        r = jsonify(
            error='category-path-invalid',
            message='Given category was not found'
        )
        r.status_code = 404
        return r

    entries = []
    for child in cursor.get_all_feeds():
        feed_id = get_hash(child.xml_url)
        with open(os.path.join(
                REPOSITORY, feed_id + '.xml'
        )) as f:
            feed = read(Feed, f)
            for entry in feed.entries:
                entries.append({
                    'title': entry.title,
                    'entry_url': url_for(
                        'feed_entry',
                        feed_id=feed_id,
                        entry_id=get_hash(entry.id),
                        _external=True
                    ),
                    'updated': entry.published_at
                })
    return jsonify(
        title=category_id,
        entries=entries
    )


@app.route('/<path:category_id>/feeds/<feed_id>/entries/')
def category_feed_entries(category_id, feed_id):
    if check_path_valid(category_id)[0]:
        return feed_entries(feed_id)
    else:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r


@app.route('/feeds/<feed_id>/entries/<entry_id>/')
def feed_entry(feed_id, entry_id):
    REPOSITORY = app.config['REPOSITORY']
    try:
        with open(os.path.join(REPOSITORY, feed_id + '.xml')) as f:
            feed = read(Feed, f)
            for entry in feed.entries:
                if entry_id == get_hash(entry.id):
                    return jsonify(
                        content=entry.content,
                        updated=entry.updated_at.__str__()
                    )
            r = jsonify(
                error='entry-not-found',
                message='Given entry does not exist'
            )
            r.status_code = 404
            return r

    except IOError:
        r = jsonify(
            error='feed-not-found',
            message='Given feed does not exist'
        )
        r.status_code = 404
        return r


@app.route('/<path:category_id>/feeds/<feed_id>/entries/<entry_id>/')
def category_feed_entry(category_id, feed_id, entry_id):
    if check_path_valid(category_id)[0]:
        return feed_entry(feed_id, entry_id)
    else:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
