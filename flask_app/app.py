from flask import Flask, render_template, jsonify, request, session, abort, redirect, flash
from flask_bcrypt import Bcrypt
import time
import threading
from datetime import datetime
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub
from pubnub.callbacks import SubscribeCallback
from pubnub.enums import PNStatusCategory
from dotenv import load_dotenv
import os
import sys
from functools import wraps

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from database import db, add_user_and_login, user_logout, is_admin, get_user_row_if_exists

# load env variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("SQL_ALCHEMY_DATABASE_URI")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

bcrypt = Bcrypt(app)

# initialise database
db.init_app(app)
with app.app_context():
    db.create_all() # create tables if not exist

# PubNub config
PUBLISH_KEY = os.getenv("PUBNUB_PUBLISH_KEY")
SUBSCRIBE_KEY = os.getenv("PUBNUB_SUBSCRIBE_KEY")
DEVICE_ID = os.getenv("PUBNUB_UUID")

# initialise PubNub
pnconfig = PNConfiguration()
pnconfig.publish_key = PUBLISH_KEY
pnconfig.subscribe_key = SUBSCRIBE_KEY
pnconfig.uuid = DEVICE_ID
pubnub = PubNub(pnconfig)

# channels
COMMAND_CHANNEL = "greenhouse_commands"
DATA_CHANNEL = "greenhouse_data"
ACK_CHANNEL = "greenhouse_ack"

# current state
greenhouse_state = {
    "temperature": 0,
    "humidity": 0,
    "led_status": False,
    "last_watered": "Not yet",
    "last_update": datetime.now().strftime("%H:%M:%S"),
    "device_online": False
}

# lock for thread-safe state updates
state_lock = threading.Lock()

class DataListener(SubscribeCallback):
    def message(self, pubnub, message):
        # handle incoming data
        global greenhouse_state
        
        msg_data = message.message
        
        if isinstance(msg_data, dict):
            with state_lock:
                if "temperature" in msg_data:
                    greenhouse_state["temperature"] = msg_data["temperature"]
                if "humidity" in msg_data:
                    greenhouse_state["humidity"] = msg_data["humidity"]
                if "led_on" in msg_data:
                    greenhouse_state["led_status"] = msg_data["led_on"]
                if "last_watered" in msg_data:
                    greenhouse_state["last_watered"] = msg_data["last_watered"]
                
                greenhouse_state["last_update"] = datetime.now().strftime("%H:%M:%S")
                greenhouse_state["device_online"] = True
    
    def status(self, pubnub, status):
        # handle connection changes
        if status.category == PNStatusCategory.PNConnectedCategory:
            print("Connected to PubNub - listening for sensor data")
        elif status.category == PNStatusCategory.PNConnectionError:
            print("PubNub connection error")
            with state_lock:
                greenhouse_state["device_online"] = False


def start_listener():
    # start PubNub listener
    listener = DataListener()
    pubnub.add_listener(listener)
    pubnub.subscribe().channels([DATA_CHANNEL]).execute()


def publish_command(command, params=None):
    # send commands
    message = {"command": command}
    if params:
        message["params"] = params
    
    try:
        envelope = pubnub.publish().channel(COMMAND_CHANNEL).message(message).sync()
        return not envelope.status.is_error()
    except Exception as e:
        print(f"PubNub error: {e}")
        return False


# authentication
def login_is_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return abort(401)
        else:
            return function(*args, **kwargs)
    return wrapper


# routes
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    # login route with password hashing
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        
        if username and password:
            # check if user exists
            user = get_user_row_if_exists(username)
            if user:
                # verify password using bcrypt
                if hasattr(user, 'password_hash') and user.password_hash:
                    # check the hashed password
                    if bcrypt.check_password_hash(user.password_hash, password):
                        session["user_id"] = username
                        session["name"] = username
                        add_user_and_login(username, username)
                        flash(f"Welcome, {username}!", "success")
                        return redirect("/dashboard")
                else:
                    flash("Problem with account.", "warning")
        
        flash("Invalid username or password", "danger")
    
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        
        if not username or not password:
            flash("Username and password are required", "danger")
        elif password != confirm_password:
            flash("Passwords do not match", "danger")
        else:
            # check if user already exists
            if get_user_row_if_exists(username):
                flash("Username already exists", "danger")
            else:
                # create password hash
                password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
                
                # create new user with password hash
                from database import User
                new_user = User(
                    name=username, 
                    user_id=username, 
                    token=None, 
                    login=1, 
                    read_access=1, 
                    write_access=1, 
                    is_admin=0,
                    password_hash=password_hash
                )
                db.session.add(new_user)
                db.session.commit()
                
                session["user_id"] = username
                session["name"] = username
                flash("Registration successful!", "success")
                return redirect("/dashboard")
    
    return render_template("register.html")


@app.route("/logout")
def logout():
    # log out user
    if "user_id" in session:
        user_logout(session["user_id"])
    session.clear()
    flash("You have been logged out.", "info")
    return redirect("/")


@app.route("/dashboard")
@login_is_required
def dashboard():
    # dashboard view (need to be logged in)
    is_user_admin = is_admin(session["user_id"])
    return render_template("dashboard.html", 
                         state=greenhouse_state,
                         username=session.get("name"),
                         is_admin=is_user_admin)


# API Routes
@app.route("/api/state")
@login_is_required
def get_state():
    # get current greenhouse state
    return jsonify(greenhouse_state)


@app.route("/api/command", methods=["POST"])
@login_is_required
def send_command():
    # send command to raspberry pi
    data = request.json
    command = data.get("command", "")
    
    response = {
        "success": False,
        "message": "Unknown command",
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }
    
    if command == "led_on":
        success = publish_command("led_on")
        response = {
            "success": success,
            "message": "LED ON command sent" if success else "Failed to send command",
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
    
    elif command == "led_off":
        success = publish_command("led_off")
        response = {
            "success": success,
            "message": "LED OFF command sent" if success else "Failed to send command",
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
    
    elif command == "water":
        success = publish_command("water")
        response = {
            "success": success,
            "message": "Water command sent" if success else "Failed to send command",
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
    
    elif command == "refresh":
        success = publish_command("refresh")
        response = {
            "success": success,
            "message": "Refresh command sent" if success else "Failed to send command",
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
    
    return jsonify(response)


if __name__ == "__main__":
    print(" http://127.0.0.1:5000")
    
    # start PubNub listener in background thread
    listener_thread = threading.Thread(target=start_listener, daemon=True)
    listener_thread.start()
    
    app.run(host="0.0.0.0", port=5000, debug=True)