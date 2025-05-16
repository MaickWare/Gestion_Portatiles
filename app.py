from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash, send_from_directory
import sqlite3
from datetime import datetime
import pandas as pd
from io import BytesIO
from fpdf import FPDF
import re
import qrcode
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = 'admin'

def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    # Tabla de propietarios
    c.execute('''
    CREATE TABLE IF NOT EXISTS propietarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        documento TEXT UNIQUE,
        nombres TEXT,
        apellidos TEXT,
        celular TEXT,
        direccion TEXT,
        correo TEXT
    )''')

    # Tabla de equipos
    c.execute('''
    CREATE TABLE IF NOT EXISTS equipos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        propietario_id INTEGER,
        marca TEXT,
        serie TEXT,
        codigo_qr TEXT UNIQUE,
        FOREIGN KEY(propietario_id) REFERENCES propietarios(id)
    )''')

    # Tabla de movimientos
    c.execute('''
    CREATE TABLE IF NOT EXISTS movimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipo_id INTEGER,
        fecha_ingreso DATETIME,
        fecha_salida DATETIME,
        FOREIGN KEY(equipo_id) REFERENCES equipos(id)
    )''')

    # Tabla de usuarios con rol
    c.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        rol TEXT CHECK(rol IN ('admin', 'usuario')) NOT NULL
    )''')

    # Crear admin por defecto si no existe
    c.execute("SELECT * FROM usuarios WHERE username = 'admin'")
    if not c.fetchone():
        c.execute("INSERT INTO usuarios (username, password, rol) VALUES (?, ?, ?)", ('admin', '1234', 'admin'))

    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.context_processor
def inject_session():
    return dict(session=session)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['username']
        contraseña = request.form['password']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios WHERE username = ? AND password = ?", (usuario, contraseña))
        user = cursor.fetchone()
        conn.close()
        if user:
            session['usuario'] = user['username']
            session['rol'] = user['rol']
            return redirect(url_for('index'))
        else:
            flash('Credenciales inválidas', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'rol' not in session or session['rol'] != 'admin':
            flash('Acceso restringido a administradores', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/registro', methods=['GET', 'POST'])
@admin_required
def registro():
    if request.method == 'POST':
        data = request.form

        # Validaciones backend
        if not re.match(r"^\d+$", data['documento']):
            return "Documento inválido. Solo números.", 400
        if not re.match(r"^[A-Za-zÁÉÍÓÚáéíóúñÑ\s]+$", data['nombres']):
            return "Nombres inválidos. Solo letras.", 400
        if not re.match(r"^[A-Za-zÁÉÍÓÚáéíóúñÑ\s]+$", data['apellidos']):
            return "Apellidos inválidos. Solo letras.", 400
        if not re.match(r"^\d{10}$", data['celular']):
            return "Celular inválido. Debe tener 10 dígitos.", 400
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", data['correo']):
            return "Correo inválido.", 400

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO propietarios (documento, nombres, apellidos, celular, direccion, correo)
                VALUES (?, ?, ?, ?, ?, ?)''',
                (data['documento'], data['nombres'], data['apellidos'], data['celular'], data['direccion'], data['correo'])
            )
            propietario_id = cursor.lastrowid

            # Generar código QR
            codigo_qr = f"{data['marca']}_{data['serie']}"
            cursor.execute('''
                INSERT INTO equipos (propietario_id, marca, serie, codigo_qr)
                VALUES (?, ?, ?, ?)''',
                (propietario_id, data['marca'], data['serie'], codigo_qr)
            )

            # Crear QR y guardarlo
            if not os.path.exists('static/qrcodes'):
                os.makedirs('static/qrcodes')
            img = qrcode.make(codigo_qr)
            img.save(f'static/qrcodes/qr_{codigo_qr}.png')

            conn.commit()
            conn.close()
            return redirect(url_for('index'))

        except sqlite3.IntegrityError:
            return "El documento ya está registrado.", 400

    return render_template('registro.html')

@app.route('/generar_qr/<codigo>')
def generar_qr(codigo):
    # Crear directorio si no existe
    if not os.path.exists('static/qrcodes'):
        os.makedirs('static/qrcodes')
    
    # Generar QR
    img = qrcode.make(codigo)
    filename = f"qr_{codigo}.png"
    img.save(f'static/qrcodes/{filename}')
    
    return send_from_directory('static/qrcodes', filename)

@app.route('/movimiento', methods=['GET', 'POST'])
def movimiento():
    if request.method == 'POST':
        codigo_qr = request.form['codigo_qr']
        conn = get_db_connection()
        
        try:
            # Buscar el equipo por código QR
            equipo = conn.execute(
                'SELECT id FROM equipos WHERE codigo_qr = ?', 
                (codigo_qr,)
            ).fetchone()
            
            if not equipo:
                flash('Equipo no encontrado', 'danger')
                return redirect(url_for('movimiento'))
            
            # Verificar si hay movimiento activo (sin fecha de salida)
            movimiento_activo = conn.execute(
                'SELECT id FROM movimientos WHERE equipo_id = ? AND fecha_salida IS NULL',
                (equipo['id'],)
            ).fetchone()
            
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            if movimiento_activo:
                # Registrar salida
                conn.execute(
                    'UPDATE movimientos SET fecha_salida = ? WHERE id = ?',
                    (now, movimiento_activo['id'])
                )
                flash('Salida registrada correctamente', 'success')
            else:
                # Registrar ingreso
                conn.execute(
                    'INSERT INTO movimientos (equipo_id, fecha_ingreso) VALUES (?, ?)',
                    (equipo['id'], now)
                )
                flash('Ingreso registrado correctamente', 'success')
            
            conn.commit()
            
        except Exception as e:
            conn.rollback()
            flash(f'Error al registrar movimiento: {str(e)}', 'danger')
        
        finally:
            conn.close()
        
        return redirect(url_for('movimiento'))
    
    return render_template('movimiento.html')

@app.route('/debug/movimientos')
def debug_movimientos():
    if 'rol' not in session or session['rol'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Obtener todos los movimientos con detalles
    movimientos = conn.execute('''
        SELECT 
            m.id as movimiento_id,
            m.fecha_ingreso,
            m.fecha_salida,
            e.id as equipo_id,
            e.marca,
            e.serie,
            e.codigo_qr,
            p.id as propietario_id,
            p.nombres,
            p.apellidos
        FROM movimientos m
        LEFT JOIN equipos e ON m.equipo_id = e.id
        LEFT JOIN propietarios p ON e.propietario_id = p.id
        ORDER BY m.fecha_ingreso DESC
    ''').fetchall()
    
    conn.close()
    
    # Convertir a lista de diccionarios para mejor visualización
    movimientos = [dict(mov) for mov in movimientos]
    
    return render_template('debug_movimientos.html', movimientos=movimientos)

@app.route('/panel')
@admin_required
def panel():
    conn = get_db_connection()
    
    # Consulta mejorada con joins correctos
    movimientos = conn.execute('''
        SELECT 
            p.nombres AS nombre,
            e.marca,
            e.serie,
            datetime(m.fecha_ingreso) as fecha_ingreso,
            datetime(m.fecha_salida) as fecha_salida
        FROM movimientos m
        JOIN equipos e ON m.equipo_id = e.id
        JOIN propietarios p ON e.propietario_id = p.id
        ORDER BY m.fecha_ingreso DESC
    ''').fetchall()
    
    conn.close()
    
    # Debug: Imprimir los resultados en consola para verificar
    print("Movimientos encontrados:", len(movimientos))
    for mov in movimientos:
        print(dict(mov))
    
    return render_template('panel.html', movimientos=movimientos)

@app.route('/export/excel')
@admin_required
def export_excel():
    conn = get_db_connection()
    movimientos = conn.execute('''
        SELECT p.nombres || ' ' || p.apellidos AS nombre,
               e.marca, e.serie, 
               m.fecha_ingreso, m.fecha_salida
        FROM movimientos m
        JOIN equipos e ON m.equipo_id = e.id
        JOIN propietarios p ON e.propietario_id = p.id
        ORDER BY m.fecha_ingreso DESC
    ''').fetchall()
    conn.close()

    # Convertir a DataFrame
    df = pd.DataFrame(movimientos, columns=['Nombre', 'Marca', 'Serie', 'Fecha Ingreso', 'Fecha Salida'])
    
    # Formatear fechas
    df['Fecha Ingreso'] = pd.to_datetime(df['Fecha Ingreso']).dt.strftime('%Y-%m-%d %H:%M:%S')
    df['Fecha Salida'] = pd.to_datetime(df['Fecha Salida']).dt.strftime('%Y-%m-%d %H:%M:%S')
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Movimientos')
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name='movimientos.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/export/pdf')
def export_pdf():
    if 'rol' not in session or session['rol'] != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    movimientos = conn.execute('''
        SELECT p.nombres || ' ' || p.apellidos AS nombre_completo,
               e.marca, e.serie, m.fecha_ingreso, m.fecha_salida
        FROM movimientos m
        JOIN equipos e ON m.equipo_id = e.id
        JOIN propietarios p ON e.propietario_id = p.id
        ORDER BY m.fecha_ingreso DESC
    ''').fetchall()
    conn.close()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.set_font("Arial", style="B", size=16)
    pdf.cell(200, 10, txt="Reporte de Movimientos", ln=True, align="C")
    pdf.ln(10)
    pdf.set_font("Arial", style="B", size=12)
    pdf.cell(50, 10, txt="Nombre Completo", border=1, align="C")
    pdf.cell(30, 10, txt="Marca", border=1, align="C")
    pdf.cell(40, 10, txt="Serie", border=1, align="C")
    pdf.cell(35, 10, txt="Fecha Ingreso", border=1, align="C")
    pdf.cell(35, 10, txt="Fecha Salida", border=1, align="C")
    pdf.ln()
    pdf.set_font("Arial", size=10)
    for mov in movimientos:
        pdf.cell(50, 10, txt=str(mov['nombre_completo']), border=1)
        pdf.cell(30, 10, txt=str(mov['marca']), border=1)
        pdf.cell(40, 10, txt=str(mov['serie']), border=1)
        pdf.cell(35, 10, txt=str(mov['fecha_ingreso']), border=1)
        pdf.cell(35, 10, txt=str(mov['fecha_salida']), border=1)
        pdf.ln()
    output = BytesIO()
    pdf.output(output)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='movimientos.pdf', mimetype='application/pdf')

@app.route('/eliminar_movimiento/<int:movimiento_id>', methods=['POST'])
def eliminar_movimiento(movimiento_id):
    if 'rol' not in session or session['rol'] != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    conn.execute('DELETE FROM movimientos WHERE id = ?', (movimiento_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('movimiento'))

if __name__ == '__main__':
    if not os.path.exists('static/qrcodes'):
        os.makedirs('static/qrcodes')
    if not os.path.exists('control.db'):
        init_db()
    app.run(debug=True)
