import os
import time
import requests
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, url_for
from flask_socketio import SocketIO, emit
from pyicloud import PyiCloudService
from http.cookiejar import LWPCookieJar
from dotenv import load_dotenv

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

# Credenciais do iCloud obtidas das variáveis de ambiente
apple_id = os.getenv('APPLE_ID')
password = os.getenv('PASSWORD')

# Nome do arquivo onde o token de sessão será armazenado
session_file = 'icloud_session.pkl'

# Inicializa o Flask e Flask-SocketIO
app = Flask(__name__, template_folder='templates')
socketio = SocketIO(app)

def authenticate():
    api = PyiCloudService(apple_id, password)
    
    if os.path.exists(session_file):
        try:
            api.session.cookies = LWPCookieJar(session_file)
            api.session.cookies.load(ignore_discard=True, ignore_expires=True)
            if not api.is_trusted_session:
                print("Sessão não confiável. Por favor, autentique novamente.")
                api.authenticate()
        except Exception as e:
            print("Erro ao carregar a sessão:", e)
            api.authenticate()
    else:
        api.authenticate()
        if api.requires_2fa:
            return api
    
    api.session.cookies.save(ignore_discard=True, ignore_expires=True)
    
    return api

api = authenticate()

@app.route('/')
def index():
    if api.requires_2fa:
        return redirect(url_for('two_factor_auth'))
    return render_template('index.html')

@app.route('/two_factor_auth', methods=['GET', 'POST'])
def two_factor_auth():
    if request.method == 'POST':
        code = request.form.get('code')
        if api.validate_2fa_code(code):
            api.trust_session()
            api.session.cookies.save(ignore_discard=True, ignore_expires=True)
            return redirect(url_for('index'))
        else:
            return render_template('two_factor_auth.html', error="Invalid code")
    return render_template('two_factor_auth.html')

last_locations = {}

def fetch_device_data():
    while True:
        socketio.emit('status', {'message': 'Iniciando a busca de dispositivos...'})
        devices_info = []
        try:
            devices = api.devices
            for device in devices:
                device_info = {
                    "name": device['name'],
                    "type": device['deviceClass'],
                    "location": None
                }
                location = device.location()
                if location:
                    latitude = location['latitude']
                    longitude = location['longitude']
                    address = get_address_from_coords(latitude, longitude)
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    device_info["location"] = {
                        "latitude": latitude,
                        "longitude": longitude,
                        "address": address,
                        "timestamp": timestamp
                    }
                    
                    # Emitir atualizações apenas quando a localização mudar ou a cada minuto
                    last_location = last_locations.get(device['name'])
                    if (not last_location or
                        last_location['latitude'] != latitude or
                        last_location['longitude'] != longitude or
                        (datetime.now() - datetime.strptime(last_location['timestamp'], '%Y-%m-%d %H:%M:%S')).seconds >= 60):
                        
                        last_locations[device['name']] = device_info["location"]
                        devices_info.append(device_info)
                        socketio.emit('update_devices', device_info)
                        socketio.emit('log', device_info)
            socketio.emit('status', {'message': 'Busca de dispositivos concluída.'})
        except Exception as e:
            socketio.emit('status', {'message': f'Erro ao buscar dispositivos: {str(e)}'})
            socketio.emit('error', {'error': str(e)})
        time.sleep(60)  # Espera 60 segundos antes de buscar novamente

def get_address_from_coords(latitude, longitude):
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={latitude}&lon={longitude}&zoom=18&addressdetails=1"
    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    if response.status_code == 200:
        data = response.json()
        if 'address' in data:
            return data['display_name']
        else:
            return "Endereço não encontrado"
    else:
        return "Erro ao buscar o endereço"

@app.route('/notifications')
def notifications():
    try:
        notifications = api.push.all()
        notifications_info = [
            {
                'title': notification.get('title', 'Sem título'),
                'message': notification.get('message', 'Sem mensagem'),
                'date': datetime.fromtimestamp(notification['date'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
            }
            for notification in notifications
        ]
        return jsonify({'notifications': notifications_info})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    emit('message', {'data': 'Connected'})
    threading.Thread(target=fetch_device_data).start()  # Inicia a busca de dados em um thread separado

@app.route('/play_sound/<device_id>')
def play_sound(device_id):
    try:
        device = next(device for device in api.devices if device['id'] == device_id)
        device.play_sound()
        return jsonify({'status': 'success', 'message': 'Sound played on device.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/lost_device/<device_id>', methods=['POST'])
def lost_device(device_id):
    try:
        data = request.json
        message = data.get('message', 'This device has been lost.')
        phone_number = data.get('phone_number', '')
        passcode = data.get('passcode', '1234')
        device = next(device for device in api.devices if device['id'] == device_id)
        device.lost_device(message, phone_number=phone_number, passcode=passcode)
        return jsonify({'status': 'success', 'message': 'Device put in lost mode.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/erase_device/<device_id>')
def erase_device(device_id):
    try:
        device = next(device for device in api.devices if device['id'] == device_id)
        device.erase()
        return jsonify({'status': 'success', 'message': 'Device erased.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=False)
