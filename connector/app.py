#import identity
#import identity.web
#import requests
import json

from flask import Flask, redirect, render_template, request, session, url_for, jsonify
from flask_session import Session
#from flask_api import status

#from flask_table import Table, Col
from flask_sqlalchemy import SQLAlchemy

from wtforms import Form, IntegerField, BooleanField, StringField, PasswordField, FormField, FieldList, validators

import asyncio

from azure.identity.aio import ClientSecretCredential

import app_config

from fido2.utils import websafe_decode

from sqlalchemy.inspection import inspect

class Serializer(object):

    def serialize(self):
        return {c: getattr(self, c) for c in inspect(self).attrs.keys()}

    @staticmethod
    def serialize_list(l):
        return [m.serialize() for m in l]

app = Flask(__name__)
app.config.from_object(app_config)
Session(app)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Avoids a warning

# Create SQLAlchemy instance
db = SQLAlchemy(app)

class ProvisionedUser(db.Model, Serializer):
    #internal to connector
    id = db.Column(db.Integer, primary_key=True)
    
    #pulled from Azure; immutable to connector
    azure_guid = db.Column(db.String(32), unique=True, nullable=False)
    display_name = db.Column(db.String(20), unique=False, nullable=False)

    #mutable to connector
    shipping_address = db.Column(db.String(20), unique=False, nullable=True)
    nk_model = db.Column(db.String(20), unique=False, nullable=True)

    fido_challenge = db.Column(db.String(240), unique=False, nullable=True)

    def __repr__(self):
        return f"id : {self.azure_guid}, name: {self.display_name}"

    def serialize(self):
        ret = Serializer.serialize(self)
        return ret

class FormUser(Form):
    id = IntegerField('id', render_kw={'readonly': True})

    azure_guid = StringField('azure_guid', render_kw={'readonly': True})
    display_name = StringField('display_name', render_kw={'readonly': True})

    shipping_address = StringField('shipping_address')
    nk_model = StringField('nk_model')

    fido_challenge = StringField('fido_challenge')

class FormOrder(Form):
    users = FieldList(FormField(FormUser), min_entries=0)

from azure.identity import EnvironmentCredential
from msgraph import GraphServiceClient

credentials = EnvironmentCredential()
scopes = ['https://graph.microsoft.com/.default']
azure_client = GraphServiceClient(credentials=credentials, scopes=scopes)

# This section is needed for url_for("foo", _external=True) to automatically
# generate http scheme when this sample is running on localhost,
# and to generate https scheme when it is deployed behind reversed proxy.
# See also https://flask.palletsprojects.com/en/2.2.x/deploying/proxy_fix/
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


#@app.route("/login")
#def login():
#    return render_template("login.html", version=identity.__version__, **auth.log_in(
#        scopes=app_config.SCOPE, # Have user consent to scopes during log-in
#        redirect_uri=url_for("auth_response", _external=True), # Optional. If present, this absolute URL must match your app's redirect_uri registered in Azure Portal
#        ))


#@app.route(app_config.REDIRECT_PATH)
#def auth_response():
#    result = auth.complete_log_in(request.args)
#    if "error" in result:
#        return render_template("auth_error.html", result=result)
#    return redirect(url_for("index"))


#@app.route("/logout")
#def logout():
#    return redirect(auth.log_out(url_for("index", _external=True)))

@app.route("/fido_response/<azid>", methods=["POST"])
async def provide_response(azid):
    #blind stub
    fidoResponse = {}
    result = await azure_client.users.by_user_id(azid).authentication.fido2_methods.create(fidoResponse)
    print(azid)

@app.route("/fido_challenge/<azid>", methods=["GET"])
async def aquire_challenge(azid):
    "#EXT#"
    g = await azure_client.users.by_user_id(azid).authentication.fido2_methods.creation_options.get()
    
    print(g)

@app.route("/update_order", methods=["POST"])
async def update_order():
    if request.form['fido_challenge_prefill']:
        az_id = request.form['fido_challenge_prefill'].id
        print(az_id)
    elif request.form['save']:
        orderForm = FormOrder(request.form)
        if orderForm.validate():
            for orderUser in orderForm.users:
                ProvisionedUser.query.\
                filter_by(azure_guid=orderUser.azure_guid.data).\
                update({'shipping_address': orderUser.shipping_address.data, 'nk_model': orderUser.nk_model.data})

        db.session.commit()

        if request.form['save'] == "Submit Order":
            return redirect('/') #todo: redirect out to Odoo checkout flow


    return redirect('/')

@app.route("/clear_order_form", methods=["POST"])
async def clear_order_form():
    ProvisionedUser.query.delete()
    db.session.commit()
    return redirect('/')

@app.route("/populate_existing_users", methods=["POST"])
async def populate_existing_users():
    users = await azure_client.users.get()

    if users and users.value:
        for user in users.value:
            if "#EXT#" not in user.user_principal_name: #Passkey registration is not supported for an external user              
                vl = ProvisionedUser.query.filter_by(azure_guid=user.id).limit(1).all()
                if not any(vl): #prevent dupes
                    p = ProvisionedUser(azure_guid=user.id, display_name=user.display_name)
                    db.session.add(p) 

    db.session.commit()
    return redirect('/')
    #return status.HTTP_200_OK

@app.route("/order_data", methods=["GET"])
async def order_data():
    users = ProvisionedUser.query.all()

    return json.dumps(ProvisionedUser.serialize_list(users))

@app.route("/", methods=["GET"])
async def index():
    ProvisionedUsers = ProvisionedUser.query.all()

    init_merits = [dict(azure_guid=u.azure_guid, display_name=u.display_name, nk_model=u.nk_model, shipping_address=u.shipping_address, fido_challenge=u.fido_challenge) for u in ProvisionedUser.query.all()]
    orderForm = FormOrder(users=init_merits)

    return render_template('index.html', ProvisionedUsers=ProvisionedUsers, orderForm=orderForm, nk_model_options=["NitroKey 3A", "NitroKey 3C"])


with app.app_context():  # Needed for DB operations
    db.create_all()      # Creates the database and tables

if __name__ == "__main__":
    app.run(debug=True)
#    app.run()
