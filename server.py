import argparse
import yaml
import logging
import database
import json
from logging.config import dictConfig
from flask import Flask, abort, jsonify, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, login_user, logout_user, current_user, login_required

logging.config.dictConfig(yaml.load(open("log/config.yml")))

app = Flask(__name__)
app.secret_key = b'h5\x1f\x91"\xfb\xc7L\xf8$\xad\xaa\xa1JQ\xf3@\xf0K\x044K\x93/'
app.config['PREFERRED_URL_SCHEME'] = 'http'
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User:
    def __init__(self, id, group='user'):
        self.id = id
        self.group = group

    def is_authenticated(self):
        return True

    def is_active(self):
        return True

    def is_anonymous(self):
        return False

    def get_id(self):
        return self.id

    def get_group(self):
        return self.group

@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login', _external=True))
    else:
        return redirect(url_for('home', _external=True))

@app.route('/signup')
def signup():
    return render_template('signup.html')

@app.route('/register_user', methods=['GET','POST'])
def register_user():
    db = database.Database()

    name = request.form['fullname']
    account = request.form['username']
    password = request.form['password']
    app.logger.info('Register user: %s (%s)', account, name)

    if account == 'labshop':
        app.logger.warn('Registration failed: %s', account)
        return render_template('signup.html', message='exist')
    if not db.add_user(account, password, 'user', name):
        app.logger.warn('Registration failed: %s', account)
        return render_template('signup.html', message='exist')
    else:
        app.logger.info('Registration successful: %s', account)
        return redirect(url_for('login', _external=True))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    db = database.Database()
    username = request.form['username']
    password = request.form['password']
    user = db.authenticate_user(username, password)
    if not user:
        app.logger.warn('Failed to login: @%s', username)
        return render_template('login.html', error=True)
    app.logger.info('Login successfully: @%s', username)
    registered_user = User(username, user['group'])
    login_user(registered_user)
    return redirect(url_for('home', _external=True))

@app.route("/logout")
@login_required
def logout():
    app.logger.info('Logout: @%s', current_user.get_id())
    logout_user()
    return redirect(url_for('login', _external=True))

@app.route('/account', methods=['GET','POST'])
@login_required
def account():
    userid = current_user.get_id()

    if request.method == 'GET':
        app.logger.info('Account: @%s', userid)
        return render_template('account.html')

    db = database.Database()
    curpass = request.form['curpass']
    newpass = request.form['newpass']
    if not db.update_password(userid, curpass, newpass):
        app.logger.info('Failed to change the password: @%s', userid)
        return render_template('account.html', message='incorrect')

    app.logger.info('Changed the password successfully: @%s', userid)
    return render_template('account.html', message='success')

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

@app.route('/home')
@login_required
def home():
    db = database.Database()
    userid = current_user.get_id()
    app.logger.info('Home: @%s', userid)
    return render_template(
        'home.html',
        user=db.get_user(userid),
        catalog=db.get_catalog(),
        records=db.get_records(userid)
        )

@app.route('/shop', methods=['GET', 'POST'])
@login_required
def shop():
    db = database.Database()
    userid = current_user.get_id()
    return render_template(
        'shop.html',
        user=db.get_user(userid),
        category=db.get_category(),
        catalog=db.get_catalog(),
        bank=db.get_bank()
        )

@app.route('/record', methods=['POST'])
@login_required
def record():
    db = database.Database()
    userid = current_user.get_id()
    jans = json.loads(request.form['cart'])
    amount = int(request.form['amount'])
    db.put_record(userid, jans)
    return redirect(url_for('shop', _external=True))

@app.route('/record_cancel/<oid>', methods=['GET', 'POST'])
@login_required
def record_cancel(oid):
    db = database.Database()
    userid = current_user.get_id()
    db.cancel_record(userid, oid)
    return redirect(url_for('home', _external=True))

@app.route('/stock', methods=['GET', 'POST'])
@login_required
def stock():
    db = database.Database()
    userid = current_user.get_id()
    return render_template(
        'stock.html',
        user=db.get_user(userid),
        category=[c for c in db.get_category() if c['cid'] != 'charge'],
        catalog=db.get_catalog()
        )

@app.route('/register_stock', methods=['GET', 'POST'])
@login_required
def register_stock():
    db = database.Database()
    userid = current_user.get_id()
    items = json.loads(request.form['items'])
    for item in items:
        db.put_stock(userid, item)
    db.update_all_stock()
    return redirect(url_for('stock', _external=True))

@app.route('/dashboard/item', methods=['GET', 'POST'])
@login_required
def dashboard_item():
    db = database.Database()
    return render_template(
        'dashboard-item.html',
        data=db.get_item_stat()
        )

@app.route('/dashboard/withdraw', methods=['GET', 'POST'])
@login_required
def dashboard_withdraw():
    db = database.Database()
    userid = current_user.get_id()

    if request.method == 'GET':
        return render_template(
            'dashboard-withdraw.html',
            withdraws=db.get_withdraws(),
            bank=db.get_bank(),
            )

    amount = request.form['amount']
    db.put_withdraw(userid, -int(amount))
    return render_template(
        'dashboard-withdraw.html',
        withdraws=db.get_withdraws(),
        bank=db.get_bank(),
        )

@app.route('/dashboard/catalog', methods=['GET', 'POST'])
@login_required
def dashboard_catalog():
    with open('item/item.yml') as fi:
        db = database.Database()
        db.build_catelog(fi)
    return redirect(url_for('dashboard_item', _external=True))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Start a process of judge server.'
        )
    parser.add_argument(
        '-p', '--port', type=int, default=5000,
        help='specify a port number'
        )
    parser.add_argument(
        '-d', '--debug', action='store_true',
        help='enable the debugging mode'
        )
    args = parser.parse_args()

    # Start the server.
    app.run(
        host='0.0.0.0',
        debug=args.debug,
        port=args.port
        )
