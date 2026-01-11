import time
import threading
import os
from gpiozero import LED, Servo
import adafruit_dht
import board

# PubNub imports
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub
from pubnub.callbacks import SubscribeCallback
from pubnub.enums import PNStatusCategory

# ==================== CONFIGURATION ====================
from dotenv import load_dotenv
load_dotenv()

# PubNub configuration
PUBLISH_KEY = os.getenv("PUBNUB_PUBLISH_KEY")
SUBSCRIBE_KEY = os.getenv("PUBNUB_SUBSCRIBE_KEY")
DEVICE_ID = os.getenv("PUBNUB_UUID")

# channel names
DATA_CHANNEL = "greenhouse_data"
COMMAND_CHANNEL = "greenhouse_commands"

# GPIO Pins
LED_PIN = 22
SERVO_PIN = 18
DHT_PIN = board.D4

# update interval (30 minutes)
UPDATE_INTERVAL = 1800

# ==================== GLOBAL VARIABLES ====================
pubnub_instance = None
latest_temp = None
latest_humidity = None
led_status = False
last_watered = None

# ==================== PUBNUB FUNCTIONS ====================
def init_pubnub(on_command_received):
    global pubnub_instance
    
    # create configuration
    pnconfig = PNConfiguration()
    pnconfig.publish_key = PUBLISH_KEY
    pnconfig.subscribe_key = SUBSCRIBE_KEY
    pnconfig.uuid = DEVICE_ID
    
    # create PubNub instance
    pubnub_instance = PubNub(pnconfig)
    
    # add listener
    class CommandListener(SubscribeCallback):
        def message(self, pubnub, message):
            _handle_incoming_message(message.message, on_command_received)
        
        def status(self, pubnub, status):
            if status.category == PNStatusCategory.PNConnectedCategory:
                print("Connected to PubNub")
    
    # subscribe to commands
    pubnub_instance.add_listener(CommandListener())
    pubnub_instance.subscribe().channels([COMMAND_CHANNEL]).execute()


def _handle_incoming_message(message_data, command_handler):
    print(message_data)
    
    # handle both string and dict commands
    if isinstance(message_data, str):
        command = message_data.lower().strip()
        params = {}
    elif isinstance(message_data, dict):
        command = message_data.get('command', '').lower().strip()
        params = message_data.get('params', {})
    else:
        print(f"Unknown message: {message_data}")
        return
    
    # call the handler function
    command_handler(command, params)


def publish_sensor_data(temperature, humidity, led_on=False, last_watered=None):    
    global pubnub_instance
    
    if not pubnub_instance:
        return False
    
    data = {
        'device': DEVICE_ID,
        'temperature': temperature,
        'humidity': humidity,
        'timestamp': time.strftime("%Y-%m-%d %H:%M:%S"),
        'led_on': led_on,
        'last_watered': last_watered
    }
    
    try:
        envelope = pubnub_instance.publish().channel(DATA_CHANNEL).message(data).sync()
        
        if not envelope.status.is_error():
            print(f"Published: {temperature}C, {humidity}%")
            return True
        else:
            print("Publish failed")
            return False
    except Exception as e:
        print(f"Publish error: {e}")
        return False


def publish_acknowledgment(command, success=True, message=""):
    global pubnub_instance
    
    if not pubnub_instance:
        return False
    
    ack_data = {
        'device': DEVICE_ID,
        'command': command,
        'success': success,
        'message': message,
        'timestamp': time.strftime("%Y-%m-%d %H:%M:%S")
    }
    
    try:
        pubnub_instance.publish().channel(f"{DEVICE_ID}_ack").message(ack_data).sync()
        print(f"Acknowledgment: {command}")
        return True
    except Exception as e:
        print(f"Failed to send acknowledgment: {e}")
        return False


def stop_pubnub():
    global pubnub_instance
    if pubnub_instance:
        pubnub_instance.unsubscribe().channels([COMMAND_CHANNEL]).execute()
        pubnub_instance = None


# ==================== HARDWARE SETUP ====================
# initialize hardware
led = LED(LED_PIN)
servo = Servo(SERVO_PIN)
dht22 = adafruit_dht.DHT22(DHT_PIN)


# ==================== SENSOR FUNCTIONS ====================
def read_sensors():
    global latest_temp, latest_humidity
    
    try:
        temperature = dht22.temperature
        humidity = dht22.humidity

        if temperature is not None and humidity is not None:
            latest_temp = round(temperature, 1)
            latest_humidity = round(humidity, 1)
            
            return True
    except RuntimeError as e:
        print(f"Sensor error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")
    
    return False


def get_sensor_data():
    # try to get fresh reading
    if not read_sensors():
        # if fresh reading fails, use last known values
    
    return latest_temp, latest_humidity


# ==================== COMMAND HANDLER ====================
def handle_command(command, params):
    # function is called when a command is received
    global led_status, last_watered
    
    print(f"command: {command}")
    
    if command in ['led_on', 'led', 'turn_led_on']:
        # turn LED on
        led.on()
        led_status = True
        publish_acknowledgment("led_on", True, "LED turned on")
        
    elif command in ['led_off', 'turn_led_off']:
        # turn LED off
        led.off()
        led_status = False
        print("LED turned OFF")
        publish_acknowledgment("led_off", True, "LED turned off")
        
    elif command in ['water', 'water_plants', 'irrigate']:
        # water plants
        try:
            # servo sequence
            servo.min()
            time.sleep(1)
            servo.mid()
            time.sleep(1)
            servo.max()
            time.sleep(1)
            servo.mid()
            
            last_watered = time.strftime("%Y-%m-%d %H:%M:%S")
            publish_acknowledgment("water", True, "Watering completed")
            
        except Exception as e:
            print(f"Watering failed: {e}")
            publish_acknowledgment("water", False, str(e))
    
    elif command in ['refresh', 'get_data', 'get_sensors', 'status']:
        # refresh and publish sensor data
        temp, humidity = get_sensor_data()
        
        success = publish_sensor_data(
            temperature=temp,
            humidity=humidity,
            led_on=led_status,
            last_watered=last_watered
        )
        
        if success:
            publish_acknowledgment("refresh", True, "Sensor data published")
        else:
            publish_acknowledgment("refresh", False, "Failed to publish")
    
    else:
        print(f"Unknown command: {command}")
        publish_acknowledgment(command, False, f"Unknown command: {command}")


# ==================== AUTO-UPDATE THREAD ====================
def auto_update():
    # updates sensor data at regular intervals
    while True:
        read_sensors()
        time.sleep(UPDATE_INTERVAL)


# ==================== MAIN ====================
if __name__ == "__main__":
    # initialize PubNub with our command handler
    init_pubnub(handle_command)
    
    # start auto-update thread
    update_thread = threading.Thread(target=auto_update, daemon=True)
    update_thread.start()
    
    try:
        # keep the program running
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("exiting...")
        
    finally:
        # cleanup
        led.off()
        dht22.exit()
        stop_pubnub()