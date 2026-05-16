# app.py
from flask import Flask, render_template, request, flash, redirect, url_for, send_from_directory, session
import os
import asyncio
import time
from datetime import datetime
from telegram import Bot
from telegram.error import InvalidToken, BadRequest, Forbidden
import logging
from werkzeug.utils import secure_filename
import re
from functools import wraps

app = Flask(__name__)
app.secret_key = 'telegram-token-checker-2024-secret-key-change-in-production'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'txt'}


ADMIN_USERNAME = '1'
ADMIN_PASSWORD = '2'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('⚠️ Please login first', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

async def check_token_async(token):
    try:
        token = token.strip()
        if not token or ':' not in token:
            return {'token': token, 'status': 'invalid', 'error': 'Invalid format'}
        
        bot = Bot(token=token)
        bot_info = await bot.get_me()
        
        return {
            'token': token,
            'status': 'active',
            'username': bot_info.username or '',
            'name': bot_info.first_name or '',
            'id': str(bot_info.id)
        }
        
    except InvalidToken:
        return {'token': token, 'status': 'invalid', 'error': 'Invalid token'}
    except BadRequest as e:
        if "Unauthorized" in str(e) or "chat not found" in str(e).lower():
            return {'token': token, 'status': 'unauthorized', 'error': 'Bot blocked or not started'}
        return {'token': token, 'status': 'error', 'error': str(e)}
    except Forbidden:
        return {'token': token, 'status': 'forbidden', 'error': 'Bot was blocked'}
    except Exception as e:
        return {'token': token, 'status': 'error', 'error': str(e)}

def check_token_sync(token):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(check_token_async(token))
        loop.close()
        return result
    except Exception as e:
        return {'token': token, 'status': 'error', 'error': str(e)}

def extract_tokens_from_text(text):
    """Извлекает токены из любого текста"""
    tokens = []
    
    # Паттерн для Telegram bot токенов: цифры:буквы_цифры
    pattern = r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b'
    
    found_tokens = re.findall(pattern, text)
    tokens.extend(found_tokens)
    
    # Удаляем дубликаты
    tokens = list(set(tokens))
    
    return tokens

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            flash('✅ Login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('❌ Invalid username or password', 'error')
    
    # Если уже авторизован
    if session.get('logged_in'):
        return redirect(url_for('index'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('👋 Logged out successfully', 'info')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=session.get('username'))

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        flash('❌ No file selected', 'error')
        return redirect(url_for('index'))
    
    file = request.files['file']
    if file.filename == '':
        flash('❌ Please select a file', 'error')
        return redirect(url_for('index'))
    
    if not allowed_file(file.filename):
        flash('❌ Only .txt files are allowed', 'error')
        return redirect(url_for('index'))
    
    start_time = time.time()
    
    try:
        filename = secure_filename(file.filename)
        if filename == '':
            filename = f'tokens_{int(time.time())}.txt'
        
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            file_content = f.read()
        
        tokens = extract_tokens_from_text(file_content)
        
        if not tokens:
            flash('❌ No Telegram bot tokens found in the file', 'error')
            os.remove(filepath)
            return redirect(url_for('index'))
        
        logger.info(f"Found {len(tokens)} unique tokens to check")
        
        results = []
        for i, token in enumerate(tokens, 1):
            logger.info(f"Checking token {i}/{len(tokens)}: {token[:15]}...")
            result = check_token_sync(token)
            results.append(result)
            
            if i % 10 == 0:
                time.sleep(0.5)
        
        valid_tokens = [r for r in results if r.get('status') == 'active']
        invalid_tokens = [r for r in results if r.get('status') != 'active']
        
        stats = {
            'total': len(results),
            'active': len(valid_tokens),
            'inactive': len(invalid_tokens)
        }
        
        processing_time = f"{time.time() - start_time:.2f} seconds"
        
        logger.info(f"Results: {len(valid_tokens)} active, {len(invalid_tokens)} inactive")
        
        os.remove(filepath)
        
        return render_template('result.html', 
                             results=results,
                             valid_tokens=valid_tokens,
                             invalid_tokens=invalid_tokens,
                             stats=stats,
                             processing_time=processing_time,
                             filename=filename,
                             checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             username=session.get('username'))
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        flash(f'❌ Error: {str(e)}', 'error')
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)
        return redirect(url_for('index'))

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    
    print("=" * 60)
    print("🔐 Telegram Token Checker - Secure Edition")
    print("👤 Admin: mr_fliver")
    print("🌐 http://localhost:8080")
    print("=" * 60)
    
    app.run(debug=True, host='0.0.0.0', port=8080)  