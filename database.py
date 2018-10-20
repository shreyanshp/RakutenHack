import datetime
import hashlib
import json
import os
import yaml
from pymongo import MongoClient
from bson.objectid import ObjectId

PWSALT_BIT = 256

def pwhash(x):
    return hashlib.sha256(x).hexdigest()

def pwsalt():
    return hashlib.sha256(os.urandom(PWSALT_BIT)).hexdigest()

def pwconcat(x, s):
    return x + "_" + s

def pwhash_with_salt(p, s):
    return pwhash(pwconcat(p, s).encode('utf-8'))

def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

class Database:
    def __init__(self, dbuser='', dbpass=''):
        self.client = MongoClient()
        self.db = self.client.shop

    def add_user(self, user, password, group, name, point=0):
        if self.get_user(user) is not None:
            return False
        
        salt = pwsalt()
        coll = self.db.user
        coll.insert_one({
            'uid': user,
            'password': pwhash_with_salt(password, salt),
            'password_salt': salt,
            'group': group,
            'name': name,
            'point': point,
            'timestamp': now()
            })
        return True

    def authenticate_user(self, user, password):
        u = self.get_user(user)
        if u is None:
            return None
        if u['password'] != pwhash_with_salt(password, u['password_salt']):
            return None
        return u

    def update_password(self, user, oldpw, newpw):
        u = self.get_user(user)
        newsalt = pwsalt()

        if u is None:
            return False

        return self.db.user.update_one(
            {'uid': user, 'password': pwhash_with_salt(oldpw, u['password_salt'])},
            {'$set': {'password': pwhash_with_salt(newpw, newsalt), 'password_salt': newsalt}}
            ).matched_count == 1

    def get_user(self, user):
        coll = self.db.user
        return coll.find_one({'uid': user})

    def build_catelog(self, fi):
        coll = self.db.catalog_new
        categories = yaml.load(fi)

        for category in categories:
            for i, item in enumerate(category['goods']):
                coll.insert({
                    'jan': str(item['jan']),
                    'name': str(item['name']),
                    'price': -int(item['price']),
                    'cost': int(item['cost']),
                    'divide': int(item['divide']),
                    'stocks': 0,
                    'category': str(category['id']),
                    'order': i
                })
        coll.rename('catalog', dropTarget=True)

        coll = self.db.category_new
        for category in categories:
            coll.insert({
                'cid': str(category['id']),
                'title': str(category['title']),
            })
        coll.rename('category', dropTarget=True)

        self.update_all_stock()

    def get_catalog(self):
        return list(self.db.catalog.find({}, {'_id': False}))

    def get_item(self, jan):
        return self.db.catalog.find_one({'jan': jan})

    def get_category(self):
        return list(self.db.category.find({}, {'_id': False}))

    def put_stock(self, user, item):
        table = self.db.stock
        table.insert({
            'jan': str(item['jan']),
            'cost': item['cost'],
            'quantity': item['quantity'],
            'divide': item['divide'],
            'user': user,
            'timestamp': now(),
        })

    def update_all_stock(self):
        for row in self.db.catalog.find():
            self.update_stock(row['jan'])

    def update_stock(self, jan):
        if jan.endswith('1000yen') or jan.endswith('500yen') or jan.endswith('50yen'):
            n = 1
        elif jan.endswith('100yen') or jan.endswith('10yen'):
            n = 4
        else:
            # Compute the number of stocks available.
            n = 0
            for row in self.db.stock.find({'jan': jan}):
                n += row['quantity'] * row['divide']
            n -= self.db.record.count({'jan': jan})
            
        # Update the number of stocks.
        self.db.catalog.update(
            {'jan': jan},
            {'$set': {'stocks': n}}
        )

    def update_all_users(self):
        for row in self.db.user.find():
            self.update_user(row['uid'])

    def update_user(self, user):
        # Compute the current balance from the records.
        point = 0
        for row in self.db.record.find({'user': user}):
            point += row['price']
        # Update the point in 'user' collection.
        self.db.user.update(
            {'uid': user},
            {'$set': {'point': point}}
        )

    def update_bank(self):
        rows = self.db.record.find(
            {'$or': [
                {'jan': '1000yen'},
                {'jan': '500yen'},
                {'jan': '100yen'},
                {'jan': '50yen'},
                {'jan': '10yen'},
                ]
            })
        point = sum(int(row['price']) for row in rows)
        self.db.bank.update(
            {'user': 'labshop'},
            {'$set': {
                'user': 'labshop',
                'amount': point,
                'timestamp': now()
                }
            },
            True
            )

    def put_record(self, user, jans):
        coll = self.db.record
        for jan in jans:
            # Retrieve the catalog entry (item) from the JAN code.
            item = self.get_item(jan)
            # Register a record for the item.
            coll.insert({
                'user': user,
                'jan': jan,
                'price': item['price'],
                'timestamp': now(),
            })

        # Update the number of stocks.
        for jan in jans:
            self.update_stock(jan)
        self.update_user(user)

        # Update the bank.
        self.update_bank()

    def charge_guest(self, amount):
        self.db.record.insert({
            'user': 'guest',
            'jan': 'guest',
            'price': amount,
            'timestamp': now()
            })

    def get_records(self, user, n=20):
        # Build a dictionary: jan -> catalog entry.
        items = {}
        for row in self.get_catalog():
            items[row['jan']] = row

        # Retrieve activity records by the user.
        rows = list(
            self.db.record.find(
                {'user': user}
                ).sort([('timestamp', -1)]).limit(n)
            )

        # Attach the item information.
        for row in rows:
            row['item'] = items[row['jan']]

        return rows

    def cancel_record(self, user, oid):
        doc = self.db.record.find_one({'_id': ObjectId(oid)})
        if doc:
            # Move the entry from record to record_cancel.
            self.db.record_cancel.insert(doc)
            self.db.record.remove(doc)
            # Update the number of stocks.
            self.update_stock(doc['jan'])
            # Update the points owned by the user.
            self.update_user(user)
    
    def put_withdraw(self, user, amount):
        self.db.bank.insert({
            'user': user,
            'amount': amount,
            'timestamp': now(),
            })
    
    def get_withdraws(self):
        return self.db.bank.find()
    
    def get_bank(self):
        rows = self.db.bank.find()
        return sum(int(row['amount']) for row in rows)

    def get_item_stat(self):
        D = []
        for item in self.db.catalog.find():
            if item['category'] == 'charge':
                continue

            jan = item['jan']
            name = item['name']
            price = -item['price']

            buy = 0
            nbuy = 0            
            for row in self.db.stock.find({'jan': jan}):
                buy += row['cost'] * row['quantity']
                nbuy += row['divide'] * row['quantity']
            cost_per_divide = buy / float(nbuy) if 0 < nbuy else 0.

            sell = 0
            nsell = 0    
            for row in self.db.record.find({'jan': jan}):
                sell -= row['price']
                nsell += 1
            
            D.append(dict(
                jan=jan,
                name=name,
                price=price,
                buy=buy,
                nbuy=nbuy,
                cost_per_divide=cost_per_divide,
                sell=sell,
                nsell=nsell,
                profit=sell-buy
            ))
        return D

if __name__ == '__main__':
    db = Database()
    db.add_user('guest', '', 'guest', 'Guest', 0)
