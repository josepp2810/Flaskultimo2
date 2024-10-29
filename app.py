from flask import Flask, render_template_string, request
import pandas as pd
import io
import numpy as np
from azure.storage.blob import BlobServiceClient
import datetime 
import os

# Crear la aplicación Flask
app = Flask(__name__)


connect_str = os.getenv('AZURE_STORAGE_KEY_FLASK')
container_name = "t1archivostablas"  # Nombre de tu contenedor
blob_service_client = BlobServiceClient.from_connection_string(connect_str)

@app.route('/', methods=['GET', 'POST'])
def index():
    
    # Obtener la fecha actual para construir el nombre del archivo
    fecha_actual = datetime.datetime.now().strftime('%m%Y')  # Formato: MMYYYY
    T1_filename = f'T1_{fecha_actual}.xlsx'
    claroscore_filename = f'Claroscore_{fecha_actual}.xlsx'
    
    # Descargar los archivos directamente desde Azure Blob Storage
    blob_client = blob_service_client.get_container_client(container_name)
    
    # Leer los archivos en DataFrames
    T1_blob = blob_client.download_blob(T1_filename).readall()
    claroscore_blob = blob_client.download_blob(claroscore_filename).readall()
    
    
    # Cargar los datos en DataFrames
    T1 = pd.read_excel(io.BytesIO(T1_blob))
    Claroscore = pd.read_excel(io.BytesIO(claroscore_blob))
    
    T1 = T1.rename(columns={'Estado de OperaciÃ³n': 'Estado de Operacion',
                            'TerminaciÃ³n de la Tarjeta': 'Terminacion de la Tarjeta',
                            })
    
    T1_fil = T1[['Fecha', 'Estado de Operacion', 'Email Cliente', 'Pedido', 'Terminacion de la Tarjeta', 'Monto',]]
    
    Claroscore_fil = Claroscore[[ 'ID de compra', 'Campo Personalizado 34' ]]
    Claroscore_fil = Claroscore_fil.drop_duplicates()
    
    merged = pd.merge(T1_fil, Claroscore_fil[['ID de compra', 'Campo Personalizado 34']],
                      how='left', left_on='Pedido', right_on='ID de compra')
    
    merged = merged.rename(columns={'Campo Personalizado 34': 'Numero de cuenta'})
    merged = merged.drop(columns=['ID de compra'])
    
    merged = pd.merge(T1_fil, Claroscore_fil[['ID de compra', 'Campo Personalizado 34']], how='left', left_on='Pedido', right_on='ID de compra')
    
    merged = merged.rename(columns={'Campo Personalizado 34': 'Numero de cuenta'})
    
    merged = merged.drop(columns=['ID de compra'])
    
    merged['Estatus Homologado'] = np.where(
        merged['Estado de Operacion'].isin(["Completada", "Cancelada", "Reembolso Parcial", "Reembolsada"]),
        "Aprobada",
        np.where(
            merged['Estado de Operacion'].isin(["Rechazada por banco", "Rechazada por antifraude", "Fallida", "Pendiente"]),
            "Rechazada",
            "Revisar registro"
        )
    )
    
    # Asegúrate de que la columna 'Fecha' está en formato de fecha
    merged['Fecha'] = pd.to_datetime(merged['Fecha'])

    # Truncar horas para mantener solo Año, Mes y Día
    merged['Fecha'] = merged['Fecha'].dt.floor('d')

    # Reemplazar NaN en 'Numero de cuenta' por 0
    merged['Numero de cuenta'] = merged['Numero de cuenta'].replace({'undefined': None}).fillna(0)

    # Asegurarse de que la columna 'Numero de cuenta' sea un entero
    merged['Numero de cuenta'] = merged['Numero de cuenta'].astype(int)

    # Obtener las fechas únicas para los filtros
    fechas_unicas = merged['Fecha'].dt.date.unique()
    fechas_unicas.sort()

    # Obtener los estados únicos para el filtro de estado de operación
    estados_unicos = merged['Estado de Operacion'].unique()

    # Inicializar variables para las fechas seleccionadas
    fecha_inicio = None
    fecha_final = None
    estados_seleccionados = []  # Inicializa una lista para los estados seleccionados
    table_html = ''  # Inicializar la variable de la tabla

    # Filtrar por fecha y estado si se envían datos del formulario
    if request.method == 'POST':
        fecha_inicio = request.form.get('fecha_inicio')
        fecha_final = request.form.get('fecha_final')
        estados_seleccionados = request.form.getlist('estado_operacion')  # Obtener lista de estados seleccionados
        ordenar_por = request.form.get('ordenar_por')  # Obtener el criterio de ordenación

        if fecha_inicio and fecha_final:
            merged = merged[(merged['Fecha'] >= fecha_inicio) & (merged['Fecha'] <= fecha_final)]

        if estados_seleccionados:
            merged = merged[merged['Estado de Operacion'].isin(estados_seleccionados)]

            # Crear la primera tabla
            resumen = merged.groupby(['Email Cliente', 'Estatus Homologado']).agg(
                Cantidad=('Monto', 'size'),  # Cuenta la cantidad de transacciones (número de filas)
                Suma_Monto=('Monto', 'sum')  # Suma los montos
            ).reset_index()

            resultado = pd.pivot_table(
                resumen, 
                index='Email Cliente', 
                columns='Estatus Homologado', 
                values=['Cantidad', 'Suma_Monto'], 
                fill_value=0
            ).reset_index()

            resultado.columns = ['Email Cliente', 
                                 'Aprobada (#)', 'Rechazada (#)', 
                                 'Aprobada ($)', 'Rechazada ($)']

            resultado['Aprobada (#)'] = resultado['Aprobada (#)'].astype(int)
            resultado['Rechazada (#)'] = resultado['Rechazada (#)'].astype(int)

            # Limpiar y convertir a float antes de la suma
            resultado['Aprobada ($)'] = resultado['Aprobada ($)'].replace({'\\$': '', ',': ''}, regex=True).astype(float)
            resultado['Rechazada ($)'] = resultado['Rechazada ($)'].replace({'\\$': '', ',': ''}, regex=True).astype(float)

            resultado['Total (#)'] = resultado['Aprobada (#)'] + resultado['Rechazada (#)']
            resultado['Total ($)'] = resultado['Aprobada ($)'] + resultado['Rechazada ($)']
            
            # Formatear montos en dólares para la visualización
            resultado['Aprobada ($)'] = resultado['Aprobada ($)'].apply(lambda x: f"${x:,.2f}")
            resultado['Rechazada ($)'] = resultado['Rechazada ($)'].apply(lambda x: f"${x:,.2f}")
            resultado['Total ($)'] = resultado['Total ($)'].apply(lambda x: f"${x:,.2f}")
            
            ## Ordenar el resultado según el filtro de ordenación
            if ordenar_por == "Aprobada (#)":
                resultado = resultado.sort_values(by='Aprobada (#)', ascending=False)
            elif ordenar_por == "Rechazada (#)":
                resultado = resultado.sort_values(by='Rechazada (#)', ascending=False)
            
            # Crear la tabla HTML de la primera tabla
            table_html += '''
            <div style="display: flex; flex-wrap: wrap; margin: 10px;">
                <div style="flex: 1; margin: 10px;">
                    <h2 style="text-align: center;">Resumen de Transacciones por Cliente</h2>
                    <table>
                        <thead>
                            <tr>
                                <th></th>
                                <th>Email Cliente</th>
                                <th>Aprobada (#)</th>
                                <th>Aprobada ($)</th>
                                <th>Rechazada (#)</th>
                                <th>Rechazada ($)</th>
                                <th>Total (#)</th>
                                <th>Total ($)</th>
                            </tr>
                        </thead>
                        <tbody>
            '''

            # Añadir filas de datos a la primera tabla HTML
            for index, row in resultado.iterrows():
                email = row['Email Cliente']
                
                # Fila principal con el botón de expansión para cuentas
                table_html += f'''
                    <tr>
                        <td><button onclick="toggleVisibility('row{index}')">+</button></td>
                        <td>{email}</td>
                        <td>{row['Aprobada (#)']}</td>
                        <td>{row['Aprobada ($)']}</td>
                        <td>{row['Rechazada (#)']}</td>
                        <td>{row['Rechazada ($)']}</td>
                        <td>{row['Total (#)']}</td>
                        <td>{row['Total ($)']}</td>
                    </tr>
                '''
                
                # Obtener números de cuenta y pedidos asociados al correo
                cuentas_asociadas = merged[['Email Cliente', 'Numero de cuenta', 'Pedido', 'Terminacion de la Tarjeta', 'Monto', 'Estatus Homologado']].drop_duplicates()
                cuentas_asociadas = cuentas_asociadas[cuentas_asociadas['Email Cliente'] == email]
                
                # Fila oculta con los números de cuenta y el monto
                table_html += f'''
                    <tr id="row{index}" class="hidden">
                        <td colspan="8" class="left-align"> <!-- Cambiar a left-align aquí -->
                            <ul>
                '''
                for cuenta_index, cuenta in cuentas_asociadas.iterrows():
                    numero_cuenta = cuenta["Numero de cuenta"]
                    pedido = cuenta["Pedido"]
                    terminacion = cuenta["Terminacion de la Tarjeta"]
                    monto = cuenta["Monto"]
                    estatus = cuenta["Estatus Homologado"]
                    
                    # Formatear monto con símbolo de dólar
                    monto_formateado = f"${monto:,.2f}"

                    # Usar un identificador único para cada elemento
                    unique_id = f'pedido{index}_{cuenta_index}'

                    # Mostrar el número de cuenta con el monto y detalles
                    table_html += f'''
                        <li>{numero_cuenta} - {estatus} - Monto: {monto_formateado}
                            <button onclick="toggleVisibility('{unique_id}')">Ver Detalles</button>
                            <ul id="{unique_id}" class="hidden">
                                <li>Pedido: {pedido}</li>
                                <li>Terminación de la Tarjeta: {terminacion}</li>
                            </ul>
                        </li>
                    '''
                table_html += '''
                            </ul>
                        </td>
                    </tr>
                '''

            # Calcular totales al final de la primera tabla
            total_aprobada_count = resultado['Aprobada (#)'].replace({',': ''}, regex=True).astype(int).sum()  # Asegúrate de que es un número
            total_rechazada_count = resultado['Rechazada (#)'].replace({',': ''}, regex=True).astype(int).sum()  # Asegúrate de que es un número
            total_aprobada_sum = resultado['Aprobada ($)'].replace({'\\$': '', ',': ''}, regex=True).astype(float).sum()  # Sumar solo valores numéricos
            total_rechazada_sum = resultado['Rechazada ($)'].replace({'\\$': '', ',': ''}, regex=True).astype(float).sum()
            total_count = total_aprobada_count + total_rechazada_count  # Esto ya es un número
            total_sum = total_aprobada_sum + total_rechazada_sum

            # Agregar fila de totales a la primera tabla
            table_html += f'''
                        <tr class="total-row">
                            <td colspan="2">Totales</td>
                            <td>{total_aprobada_count:,}</td> <!-- Formatear con separadores de miles -->
                            <td>${total_aprobada_sum:,.2f}</td> <!-- Asegúrate de que esto es float -->
                            <td>{total_rechazada_count:,}</td> <!-- Formatear con separadores de miles -->
                            <td>${total_rechazada_sum:,.2f}</td> <!-- Asegúrate de que esto es float -->
                            <td>{total_count:,}</td> <!-- Formatear con separadores de miles -->
                            <td>${total_sum:,.2f}</td> <!-- Asegúrate de que esto es float -->
                        </tr>
                        </tbody>
                    </table>
                </div>
            </div>
            '''
            # Crear la segunda tabla usando la lógica proporcionada
            merged_2 = merged.copy()  # Hacer una copia de merged
            merged_2['Numero de cuenta'] = merged_2['Numero de cuenta'].fillna('0')  # Reemplazar NaN por '0'

            # Agrupar por 'Numero de cuenta' y 'Estatus Homologado'
            resumen2 = merged_2.groupby(['Numero de cuenta', 'Estatus Homologado'], dropna=False).agg(
                Cantidad=('Monto', 'size'),  # Cuenta la cantidad de transacciones (número de filas)
                Suma_Monto=('Monto', 'sum')  # Suma los montos
            ).reset_index()

            # Crear el DataFrame final con la estructura deseada
            resultado2 = pd.pivot_table(
                resumen2, 
                index='Numero de cuenta', 
                columns='Estatus Homologado', 
                values=['Cantidad', 'Suma_Monto'], 
                fill_value=0
            ).reset_index()

            # Renombrar las columnas para que sean más claras
            resultado2.columns = ['Numero de cuenta', 
                                  'Aprobada (#)', 'Rechazada (#)', 
                                  'Aprobada ($)', 'Rechazada ($)']

            # Asegurar que las columnas de cantidad sean enteros
            resultado2['Aprobada (#)'] = resultado2['Aprobada (#)'].replace({',': ''}, regex=True).astype(int)
            resultado2['Rechazada (#)'] = resultado2['Rechazada (#)'].replace({',': ''}, regex=True).astype(int)
            
            # Ordenar el resultado de la segunda tabla según el filtro de ordenación
            if ordenar_por == "Aprobada (#)":
                resultado2 = resultado2.sort_values(by='Aprobada (#)', ascending=False)
            elif ordenar_por == "Rechazada (#)":
                resultado2 = resultado2.sort_values(by='Rechazada (#)', ascending=False)
            
            # Formatear columnas de cantidad con separadores de miles
            resultado2['Aprobada (#)'] = resultado2['Aprobada (#)'].apply(lambda x: f"{x:,}")
            resultado2['Rechazada (#)'] = resultado2['Rechazada (#)'].apply(lambda x: f"{x:,}")
            
            # Calcular 'Total (#)' antes de formatear
            resultado2['Total (#)'] = resultado2['Aprobada (#)'].replace({',': ''}, regex=True).astype(int) + resultado2['Rechazada (#)'].replace({',': ''}, regex=True).astype(int)
            
            # Formatear 'Total (#)' con separadores de miles
            resultado2['Total (#)'] = resultado2['Total (#)'].apply(lambda x: f"{x:,}")
            
            # Agregar la columna de 'Total ($)' que será la suma de los montos
            resultado2['Total ($)'] = resultado2['Aprobada ($)'] + resultado2['Rechazada ($)']
            
            # Formatear montos en dólares
            resultado2['Aprobada ($)'] = resultado2['Aprobada ($)'].apply(lambda x: f"${x:,.2f}")
            resultado2['Rechazada ($)'] = resultado2['Rechazada ($)'].apply(lambda x: f"${x:,.2f}")
            resultado2['Total ($)'] = resultado2['Total ($)'].apply(lambda x: f"${x:,.2f}")

            # Obtener correos a la tabla de resultados
            correo_por_cuenta = merged[['Numero de cuenta', 'Email Cliente']].drop_duplicates()

            # Tabla 2: Resumen por número de cuenta
            table_html += '''
                <div style="flex: 1; margin: 10px;">
                    <h2 style="text-align: center;">Resumen de Transacciones por Número de Cuenta</h2>
                    <table>
                        <thead>
                            <tr>
                                <th></th>
                                <th>Numero de Cuenta</th>
                                <th>Aprobada (#)</th>
                                <th>Aprobada ($)</th>
                                <th>Rechazada (#)</th>
                                <th>Rechazada ($)</th>
                                <th>Total (#)</th>
                                <th>Total ($)</th>
                            </tr>
                        </thead>
                        <tbody>
            '''

            # Añadir filas de datos a la segunda tabla HTML
            for index, row in resultado2.iterrows():
                numero_cuenta = row['Numero de cuenta']
                
                # Fila principal con el botón de expansión para correos
                table_html += f'''
                    <tr>
                        <td><button onclick="toggleVisibility('row2_{index}')">+</button></td>
                        <td>{numero_cuenta}</td>
                        <td>{row['Aprobada (#)']}</td>
                        <td>{row['Aprobada ($)']}</td>
                        <td>{row['Rechazada (#)']}</td>
                        <td>{row['Rechazada ($)']}</td>
                        <td>{row['Total (#)']}</td>
                        <td>{row['Total ($)']}</td>
                    </tr>
                '''
                
                # Obtener correos asociados al número de cuenta
                correos_asociados = correo_por_cuenta[correo_por_cuenta['Numero de cuenta'] == numero_cuenta]
                
                # Fila oculta con los correos asociados
                table_html += f'''
                    <tr id="row2_{index}" class="hidden">
                        <td colspan="8" class="left-align"> <!-- Cambiar a left-align aquí -->
                            <ul>
                '''
                for _, correo in correos_asociados.iterrows():
                    email_cliente = correo['Email Cliente']
                    # Mostrar el correo asociado
                    table_html += f'<li>{email_cliente}</li>'
                
                table_html += '''
                            </ul>
                        </td>
                    </tr>
                '''

            # Calcular totales al final de la segunda tabla
            total_aprobada_count_2 = resultado2['Aprobada (#)'].replace({',': ''}, regex=True).astype(int).sum()
            total_rechazada_count_2 = resultado2['Rechazada (#)'].replace({',': ''}, regex=True).astype(int).sum()
            total_aprobada_sum_2 = resultado2['Aprobada ($)'].replace({'\\$': '', ',': ''}, regex=True).astype(float).sum()
            total_rechazada_sum_2 = resultado2['Rechazada ($)'].replace({'\\$': '', ',': ''}, regex=True).astype(float).sum()
            total_count_2 = total_aprobada_count_2 + total_rechazada_count_2
            total_sum_2 = total_aprobada_sum_2 + total_rechazada_sum_2

            # Agregar fila de totales a la segunda tabla
            table_html += f'''
                        <tr class="total-row">
                            <td colspan="2">Totales</td>
                            <td>{total_aprobada_count_2:,}</td>
                            <td>${total_aprobada_sum_2:,.2f}</td>
                            <td>{total_rechazada_count_2:,}</td>
                            <td>${total_rechazada_sum_2:,.2f}</td>
                            <td>{total_count_2:,}</td>
                            <td>${total_sum_2:,.2f}</td>
                        </tr>
                        </tbody>
                    </table>
                </div>
            '''
            # Crear la tercera tabla para contar correos distintos
            resumen3 = merged_2.groupby(['Numero de cuenta', 'Estatus Homologado'], dropna=False).agg(
                Correos_Distintos=('Email Cliente', 'nunique')  # Cuenta los correos distintos
            ).reset_index()

            # Crear el DataFrame final con la estructura deseada
            resultado3 = pd.pivot_table(
                resumen3, 
                index='Numero de cuenta', 
                columns='Estatus Homologado', 
                values='Correos_Distintos', 
                fill_value=0
            ).reset_index()

            # Renombrar las columnas para que sean más claras
            resultado3.columns = ['Numero de cuenta', 
                                  'Aprobada (Correos Distintos)', 
                                  'Rechazada (Correos Distintos)']
            
            # Asegurar que los valores sean enteros
            resultado3['Aprobada (Correos Distintos)'] = resultado3['Aprobada (Correos Distintos)'].astype(int)
            resultado3['Rechazada (Correos Distintos)'] = resultado3['Rechazada (Correos Distintos)'].astype(int)
            
            
            if ordenar_por == "Aprobada (#)":
                resultado3 = resultado3.sort_values(by='Aprobada (Correos Distintos)', ascending=False)
            elif ordenar_por == "Rechazada (#)":
                resultado3 = resultado3.sort_values(by='Rechazada (Correos Distintos)', ascending=False)
            
            # Función para formatear números con separadores de miles
            def format_thousands(x):
                return f"{x:,}"
            
            # Tabla 3: Correos Distintos por Número de Cuenta con jerarquía de correos
            table_html += '''
                <div style="flex: 1; margin: 10px;">
                    <h2 style="text-align: center;">Correos Distintos por Número de Cuenta</h2>
                    <table>
                        <thead>
                            <tr>
                                <th></th>
                                <th>Numero de Cuenta</th>
                                <th>Aprobada (Correos Distintos)</th>
                                <th>Rechazada (Correos Distintos)</th>
                                <th>Total (Correos Distintos)</th>
                            </tr>
                        </thead>
                        <tbody>
            '''

            # Añadir filas de datos a la tercera tabla HTML
            for index, row in resultado3.iterrows():
                numero_cuenta = row['Numero de cuenta']
                
                # Calcular el total para cada fila
                total_correos_distintos = row['Aprobada (Correos Distintos)'] + row['Rechazada (Correos Distintos)']
                
                # Fila principal con el botón de expansión
                table_html += f'''
                    <tr>
                        <td><button onclick="toggleVisibility('row3_{index}')">+</button></td>
                        <td>{numero_cuenta}</td>
                        <td>{format_thousands(row['Aprobada (Correos Distintos)'])}</td>
                        <td>{format_thousands(row['Rechazada (Correos Distintos)'])}</td>
                        <td>{format_thousands(total_correos_distintos)}</td>
                    </tr>
                '''
                
                # Obtener correos asociados al número de cuenta
                correos_asociados = correo_por_cuenta[correo_por_cuenta['Numero de cuenta'] == numero_cuenta]
                
                # Fila oculta con los correos asociados
                table_html += f'''
                    <tr id="row3_{index}" class="hidden">
                        <td colspan="4" class="left-align"> <!-- Cambiar a left-align aquí -->
                            <ul>
                '''
                for _, correo in correos_asociados.iterrows():
                    email_cliente = correo['Email Cliente']
                    # Mostrar el correo asociado
                    table_html += f'<li>{email_cliente}</li>'
                
                table_html += '''
                            </ul>
                        </td>
                    </tr>
                '''

            # Calcular totales al final de la tercera tabla
            total_correos_aprobados = resultado3['Aprobada (Correos Distintos)'].sum()
            total_correos_rechazados = resultado3['Rechazada (Correos Distintos)'].sum()
            total_correos = total_correos_aprobados + total_correos_rechazados

            # Agregar fila de totales a la tercera tabla
            table_html += f'''
                        <tr class="total-row">
                            <td>Total</td>
                            <td></td>
                            <td>{format_thousands(total_correos_aprobados)}</td>
                            <td>{format_thousands(total_correos_rechazados)}</td>
                            <td>{format_thousands(total_correos)}</td>
                        </tr>
                    </tbody>
                </table>
                </div>
            </div>
            </body>
            </html>
            '''
# Renderizar la plantilla
    return render_template_string('''  
    <!doctype html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Resumen de Transacciones</title>
        <style>
            body {
                font-family: 'Calibri Math', sans-serif; /* Tipografía Calibri Math */
            }
            .hidden { display: none; }
            .table-container { margin-bottom: bold; }
            .total-row { font-weight: bold; }
            .tables-wrapper {
                display: flex;
                justify-content: space-between;/* Mantiene el espacio entre las tablas */
                flex-wrap: nowrap; /* Mantiene las tablas en una sola línea */
                overflow-x: auto; /* Permite desplazamiento horizontal si no cabe en pantalla */
                width: 100%; /* Asegura que ocupe el 100% del contenedor */
            }
            .table-container {
                width: 33%; /* Ajusta el ancho de las tablas para que entren las tres lado a lado */
                box-sizing: border-box;
                min-width: 300px; /* Añade un ancho mínimo para las tablas */
                margin: 0 10px; /* Espacio entre tablas */
            }
            table {
                border-collapse: collapse;
                width: 100%;
                border: 1px solid black;
            }
            th, td {
                border: 1px solid black;
                padding: 8px;
                text-align: right;
            }
            th {
                background-color: #004080; /* Color de fondo para los encabezados */
                color: white; /* Cambiar el color del texto a blanco */
                text-align: center; /* Centrar texto en los encabezados */
            }
            h1 {
                text-transform: uppercase; /* Título en mayúsculas */
                text-align: center; /* Centrar el título principal */
            }
            .form-container {
                display: flex;
                align-items: center; /* Alinea verticalmente los elementos del formulario */
                gap: 10px; /* Espacio entre los elementos */
            }
            
            select {
                background-color: #004080; /* Color de fondo de los cuadros de selección */
                color: white; /* Color de texto en los cuadros de selección */
                border: 1px solid #ccc; /* Borde del cuadro de selección */
                padding: 10px; /* Espaciado interno */
                font-size: 16px; /* Aumentar el tamaño de la fuente */
            }
        </style>
        <script>
            function toggleVisibility(id) {
                var row = document.getElementById(id);
                if (row.classList.contains('hidden')) {
                    row.classList.remove('hidden');
                } else {
                    row.classList.add('hidden');
                }
            }
        </script>
    </head>
    <body>
    
        <h1>Resumen de Transacciones</h1>
        <form method="post" class="form-container">
            <label for="estado_operacion">Estado de Operación:</label>
            <select id="estado_operacion" onchange="toggleVisibility('checkboxes')">
                <option value="">Seleccione</option>
                <option value="Mostrar">Mostrar opciones</option>
            </select>
        
            <div id="checkboxes" class="hidden">
                {% for estado in estados_unicos %}
                <label>
                    <input type="checkbox" name="estado_operacion" value="{{ estado }}">
                    {{ estado }}
                </label><br>
                {% endfor %}
            </div>
        
            <label for="fecha_inicio">Fecha Inicio:</label>
            <select name="fecha_inicio" id="fecha_inicio">
                <option value="">Seleccione</option>
                {% for fecha in fechas_unicas %}
                <option value="{{ fecha }}">{{ fecha }}</option>
                {% endfor %}
            </select>
            
            <label for="fecha_final">Fecha Final:</label>
            <select name="fecha_final" id="fecha_final">
                <option value="">Seleccione</option>
                {% for fecha in fechas_unicas %}
                <option value="{{ fecha }}">{{ fecha }}</option>
                {% endfor %}
            </select>
            
            <label for="ordenar_por">Ordenar por:</label>
            <select name="ordenar_por" id="ordenar_por">
                <option value="">Seleccione</option>
                <option value="Aprobada (#)">Aprobada (#)</option>
                <option value="Rechazada (#)">Rechazada (#)</option>
            </select>
            
            <button type="submit">Filtrar</button>
        </form>

        <div class="tables-wrapper">
            {{ table_html | safe }}
        </div>
    </body>
    </html>
    ''', table_html=table_html, fechas_unicas=fechas_unicas, estados_unicos=estados_unicos)

if __name__ == '__main__':
    app.run(debug=True)
    
    #######################################ESTE ES PERFECTO X2##################################
