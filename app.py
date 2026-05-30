from flask import Flask, jsonify, request
from flask_cors import CORS
import psycopg2
import json
import math

app = Flask(__name__)
CORS(app)

# ==========================================
# CONEXIÓN A BASE DE DATOS
# ==========================================
def obtener_conexion():
    return psycopg2.connect(
        host="localhost",
        database="geoportal_vial",
        user="user",       # Cambia esto por tus credenciales
        password="user",   # Cambia esto por tus credenciales
        port="5432"
    )

# ==========================================
# CONSULTA DE PREDIO POR MATRÍCULA
# ==========================================
@app.route('/consulta/<matricula>')
def consulta(matricula):
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        sql = """
        SELECT
            p.matricula, p.npn_predio, p.direccion,
            v.norma, v.nombre as via, v.ancho_min, v.ancho_max,
            ST_X(ST_Centroid(p.geom)) as x, ST_Y(ST_Centroid(p.geom)) as y,
            ST_AsGeoJSON(p.geom) as geom
        FROM predios p
        LEFT JOIN vias v 
        ON ST_DWithin(p.geom::geography, v.geom::geography, 30)
        WHERE p.matricula::text = %s
        LIMIT 1
        """
        cursor.execute(sql, (str(matricula).strip(),))
        fila = cursor.fetchone()

        resultado = []
        if fila:
            # Eliminar los ceros de los anchos convirtiendo a entero
            a_min = str(int(math.ceil(float(fila[5])))) if fila[5] else "-"
            a_max = str(int(math.ceil(float(fila[6])))) if fila[6] else "-"

            resultado.append({
                "matricula": fila[0],
                "npn_predio": fila[1],
                "direccion": fila[2],
                "norma": fila[3] if fila[3] else "Sin norma",
                "via": fila[4] if fila[4] else "N/A",
                "ancho_min": a_min,
                "ancho_max": a_max,
                "x": float(fila[7]),
                "y": float(fila[8]),
                "geom": json.loads(fila[9])
            })

        cursor.close()
        conexion.close()
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==========================================
# IDENTIFICACIÓN POR CLIC EN EL MAPA
# ==========================================
@app.route('/identificar')
def identificar():
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        
        cursor.execute("""
            SELECT matricula, npn_predio, direccion FROM predios 
            WHERE ST_Intersects(geom, ST_SetSRID(ST_Point(%s, %s), 4326)) LIMIT 1
        """, (lng, lat))
        p = cursor.fetchone()
        
        cursor.execute("""
            SELECT nombre, norma, ancho_min, ancho_max FROM vias 
            WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_Point(%s, %s), 4326)::geography, 20)
            ORDER BY ST_Distance(geom::geography, ST_SetSRID(ST_Point(%s, %s), 4326)::geography) ASC LIMIT 1
        """, (lng, lat, lng, lat))
        v = cursor.fetchone()
        
        res = {"predio": None, "via": None}
        if p: res["predio"] = {"matricula": p[0], "npn": p[1], "direccion": p[2]}
        if v: 
            a_min = str(int(math.ceil(float(v[2])))) if v[2] else "-"
            a_max = str(int(math.ceil(float(v[3])))) if v[3] else "-"
            res["via"] = {"nombre": v[0], "norma": v[1], "ancho_min": a_min, "ancho_max": a_max}
            
        cursor.close()
        conexion.close()
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==========================================
# REGISTRAR SOLICITUD CON TODOS LOS DATOS
# ==========================================
@app.route('/registrar', methods=['POST'])
def registrar():
    try:
        datos = request.json
        matricula = datos['matricula']
        
        nombre = datos.get('nombre', 'N/A')
        documento = datos.get('documento', 'N/A')
        correo = datos.get('correo', 'N/A')
        barrio = datos.get('barrio', 'N/A')
        obs_usuario = datos.get('observacion', '')

        if not obs_usuario.strip():
            obs_usuario = "Sin observaciones adicionales."

        obs_completa = f"Solicitante: {nombre} (Doc: {documento}) | Correo: {correo} | Barrio: {barrio} || Detalle: {obs_usuario}"

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        cursor.execute("SELECT matricula, npn_predio, direccion FROM predios WHERE matricula::text = %s LIMIT 1", (str(matricula).strip(),))
        predio = cursor.fetchone()

        if not predio:
            return jsonify({"mensaje": "Predio no encontrado"}), 404

        cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM solicitudes")
        consecutivo = cursor.fetchone()[0]
        radicado = f"GV-2026-{str(consecutivo).zfill(6)}"

        sql = """
        INSERT INTO solicitudes(radicado, matricula, npn, direccion, observacion, estado, fecha)
        VALUES(%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(sql, (radicado, predio[0], predio[1], predio[2], obs_completa, 'Radicada'))
        conexion.commit()
        cursor.close()
        conexion.close()

        return jsonify({
            "mensaje": "Solicitud registrada", 
            "radicado": radicado, 
            "estado": "Radicada"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==========================================
# LISTAR SOLICITUDES (ROL ADMIN)
# ==========================================
@app.route('/solicitudes')
def solicitudes():
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        
        sql = """
        SELECT s.radicado, s.matricula, s.direccion, s.observacion, s.estado, s.fecha::text,
               ST_X(ST_Centroid(p.geom)) as x, ST_Y(ST_Centroid(p.geom)) as y
        FROM solicitudes s
        LEFT JOIN predios p ON s.matricula::text = p.matricula::text
        ORDER BY s.fecha DESC
        """
        cursor.execute(sql)
        filas = cursor.fetchall()

        resultado = [{
            "radicado": f[0], "matricula": f[1], "direccion": f[2], 
            "observacion": f[3], "estado": f[4], "fecha": f[5],
            "x": f[6], "y": f[7]
        } for f in filas]

        cursor.close()
        conexion.close()
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/actualizar_estado', methods=['POST'])
def actualizar_estado():
    datos = request.json
    conexion = obtener_conexion()
    cursor = conexion.cursor()
    cursor.execute("UPDATE solicitudes SET estado = %s WHERE radicado = %s", (datos['estado'], datos['radicado']))
    conexion.commit()
    return jsonify({"mensaje": "Estado actualizado"})

@app.route('/eliminar_solicitud/<radicado>', methods=['DELETE'])
def eliminar_solicitud(radicado):
    conexion = obtener_conexion()
    cursor = conexion.cursor()
    cursor.execute("DELETE FROM solicitudes WHERE radicado = %s", (radicado,))
    conexion.commit()
    return jsonify({"mensaje": "Solicitud eliminada"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)