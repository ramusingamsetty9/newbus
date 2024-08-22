import os
import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from flask import Flask, render_template, request
from datetime import datetime

app = Flask(__name__)

# Define the path for the Excel file
EXCEL_FILE = 'bus_data.xlsx'

# Ensure the previous Excel file is deleted before creating a new one
if os.path.exists(EXCEL_FILE):
    os.remove(EXCEL_FILE)

# Function to set up the WebDriver
def setup_driver():
    service = Service(ChromeDriverManager().install())
    options = webdriver.ChromeOptions()
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# Function to generate the URL for scraping
def generate_url(source, destination, travel_date, bus_type):
    base_url = "https://www.redbus.in/bus-tickets"
    query_params = f"{source.replace(' ', '-').lower()}-to-{destination.replace(' ', '-').lower()}?fromCityName={source}&toCityName={destination}&onward={travel_date}&busType={bus_type}"
    url = f"{base_url}/{query_params}"
    return url

# Function to scroll the webpage to load all content
def scroll_to_load_all(driver):
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)  # Wait for new content to load
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:  # No more new content
            break
        last_height = new_height

# Function to fetch data from RedBus
def fetch_redbus_data(source, destination, travel_date, bus_type):
    url = generate_url(source, destination, travel_date, bus_type)
    driver = setup_driver()
    driver.get(url)

    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, 'bus-item')))
    except Exception as e:
        print(f"Error: {e}")
        driver.quit()
        return pd.DataFrame()

    scroll_to_load_all(driver)  # Ensure all buses are loaded by scrolling

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    driver.quit()

    buses = soup.find_all('div', class_='bus-item')
    data = []
    for bus in buses:
        try:
            travel_name = bus.find('div', class_='travels').text.strip()
            bus_type_extracted = bus.find('div', class_='bus-type').text.strip()
            departure_time = bus.find('div', class_='dp-time').text.strip()
            duration = bus.find('div', class_='dur').text.strip()
            fare_element = bus.find('div', class_='fare d-block').text.strip()
            fare = fare_element.replace('₹', '').replace(',', '').replace('INR ', '')
            amenities_elements = bus.find_all('div', class_='amenities-item')
            amenities = ', '.join([amenity.text.strip() for amenity in amenities_elements])
            seats_remaining = bus.find('div', class_='seat-left').text.strip().replace(' Seats Left', '')

            fare = int(float(fare)) if fare else 0

            if any(keyword.lower() in bus_type_extracted.lower() for keyword in ['b11r', '9600', 'volvo', 'scania', 'multi axle']):
                amenities += f", {bus_type_extracted}"

            data.append({
                'Travel Name': travel_name,
                'Bus Type': bus_type_extracted,
                'Seat Type': bus_type,  # Assuming seat type is part of bus_type for simplicity
                'Departure Time': departure_time,
                'Duration': duration,
                'Date': travel_date,
                'Fare': fare,
                'Amenities': amenities,
                'Seats Remaining': seats_remaining
            })
        except Exception as e:
            print(f"Error processing bus: {e}")

    return pd.DataFrame(data)

# Function to save data to Excel
def save_to_excel(data, filename):
    data.to_excel(filename, index=False)
    print(f"Data saved to {filename}")

# Function to parse time from a string
def parse_time(time_str):
    try:
        return datetime.strptime(time_str, "%I:%M %p").time()
    except ValueError:
        return datetime.strptime(time_str, "%H:%M").time()

# Function to calculate fare based on various factors
def calculate_fare(base_price, amenities, departure_time, seat_position, seat_type):
    if base_price is None or pd.isna(base_price):
        base_price = 0
    
    if 'WiFi' in amenities:
        base_price *= 1.02
    if 'Washroom' in amenities:
        base_price *= 1.05
    if 'Multi-axle' in amenities:
        base_price *= 1.02
    if '9600' in amenities:
        base_price *= 1.015

    departure_time = parse_time(departure_time)
    if departure_time >= datetime.strptime("18:00", "%H:%M").time() and departure_time <= datetime.strptime("20:00", "%H:%M").time():
        base_price *= 1.05
    elif departure_time > datetime.strptime("20:00", "%H:%M").time() and departure_time <= datetime.strptime("23:00", "%H:%M").time():
        base_price *= 1.06
    elif departure_time > datetime.strptime("23:00", "%H:%M").time():
        base_price *= 0.98

    if seat_position == 'upper':
        base_price *= 0.995
    elif seat_position == 'lower':
        base_price *= 1.00

    if seat_type == 'single':
        base_price *= 1.07
    elif seat_type == 'double_vacant':
        pass
    elif seat_type == 'double_one_filled':
        base_price *= 0.99

    return round(base_price, 2)

# Function to calculate fares for each seat
def calculate_seat_fares(source, destination, bus_type, seating_type, amenities, departure_date, departure_time, rows, columns, num_seats, num_berths):
    data = pd.read_excel(EXCEL_FILE)
    base_price = data['Fare'].mean()

    suggested_fares = []
    for row in range(rows):
        row_fares = []
        for col in range(columns):
            if seating_type == 'Sleeper':
                seat_position = 'upper' if row < rows // 2 else 'lower'
                seat_type = 'single'
            elif seating_type == 'Seater + Sleeper':
                if row < num_seats:
                    seat_position = 'lower'
                    seat_type = 'double'
                else:
                    seat_position = 'upper'
                    seat_type = 'single'
            else:
                seat_position = 'lower'
                seat_type = 'double'

            fare = calculate_fare(base_price, amenities, departure_time, seat_position, seat_type)
            row_fares.append(fare)
        suggested_fares.append(row_fares)

    return suggested_fares

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        source = request.form.get('source')
        destination = request.form.get('destination')
        bus_type = request.form.get('bus_type')
        seating_type = request.form.get('seating_type')
        amenities = request.form.getlist('amenities')
        departure_date = request.form.get('departure_date')
        departure_time = request.form.get('departure_time')

        rows = int(request.form.get('rows', 0))
        columns = int(request.form.get('columns', 0))
        num_seats = int(request.form.get('num_seats', 0))
        num_berths = int(request.form.get('num_berths', 0))

        # Fetch data and save to Excel
        bus_data = fetch_redbus_data(source, destination, departure_date, bus_type)
        save_to_excel(bus_data, EXCEL_FILE)

        # Calculate fares
        suggested_fares = calculate_seat_fares(source, destination, bus_type, seating_type, amenities, departure_date, departure_time, rows, columns, num_seats, num_berths)

        return render_template('index.html', rows=rows, columns=columns, suggested_fares=suggested_fares, bus_type=bus_type, seating_type=seating_type)

    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
