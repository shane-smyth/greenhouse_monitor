import time
import threading
from gpiozero import LED, Servo
import adafruit_dht
import board

# --------------------
# GPIO SETUP
# --------------------
LED_PIN = 22
SERVO_PIN = 18
DHT_PIN = board.D4

led = LED(LED_PIN)
servo = Servo(SERVO_PIN)
dht22 = adafruit_dht.DHT22(DHT_PIN)

# --------------------
# GLOBAL STATE
# --------------------
latest_temperature = None
latest_humidity = None
last_updated = None

UPDATE_INTERVAL = 1800  # 30 minutes (in seconds)

# --------------------
# SENSOR READING
# --------------------
def read_dht22():
    global latest_temperature, latest_humidity, last_updated
    try:
        temperature = dht22.temperature
        humidity = dht22.humidity

        if temperature is not None and humidity is not None:
            latest_temperature = temperature
            latest_humidity = humidity
            last_updated = time.strftime("%Y-%m-%d %H:%M:%S")

            print(f"[DHT22] Temp: {temperature:.1f}°C | Humidity: {humidity:.1f}%")
    except RuntimeError as e:
        print(f"[DHT22 ERROR] {e}")

# --------------------
# AUTO UPDATE THREAD
# --------------------
def auto_update():
    while True:
        read_dht22()
        time.sleep(UPDATE_INTERVAL)

# --------------------
# SERVO CONTROL
# --------------------
def water_plants():
    print("💧 Watering plants...")
    servo.min()
    time.sleep(1)
    servo.mid()
    time.sleep(1)
    servo.max()
    time.sleep(1)
    servo.mid()
    print("✅ Watering complete")

# --------------------
# MAIN LOOP
# --------------------
def main():
    print("🌱 Greenhouse Hardware Test Started")
    print("Commands:")
    print("  led on      -> Turn LED on")
    print("  led off     -> Turn LED off")
    print("  water       -> Activate servo")
    print("  read        -> Read DHT22 now")
    print("  status      -> Show last sensor data")
    print("  exit        -> Quit")

    # Start background sensor updates
    thread = threading.Thread(target=auto_update, daemon=True)
    thread.start()

    while True:
        command = input("> ").strip().lower()

        if command == "led on":
            led.on()
            print("💡 LED ON")

        elif command == "led off":
            led.off()
            print("💡 LED OFF")

        elif command == "water":
            water_plants()

        elif command == "read":
            read_dht22()

        elif command == "status":
            print(f"Temp: {latest_temperature}°C")
            print(f"Humidity: {latest_humidity}%")
            print(f"Last Update: {last_updated}")

        elif command == "exit":
            print("👋 Exiting...")
            break

        else:
            print("❓ Unknown command")

    led.off()
    dht22.exit()

if __name__ == "__main__":
    main()
