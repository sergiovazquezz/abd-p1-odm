__author__ = 'Pablo Ramos Criado'
__students__ = 'Sergio_Vázquez Samuel_Millan'


from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import time
from typing import Generator, Any, Self
from geojson import Point
import pymongo
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from bson.objectid import ObjectId

import yaml

def getLocationPoint(address: str) -> Point:
    """
    Obtiene las coordenadas de una dirección en formato geojson.Point
    Utilizar la API de geopy para obtener las coordenadas de la direccion
    Cuidado, la API es publica tiene limite de peticiones, utilizar sleeps.

    Parameters
    ----------
        address : str
            direccion completa de la que obtener las coordenadas
    Returns
    -------
        geojson.Point
            coordenadas del punto de la direccion
    """
    geolocator = Nominatim(user_agent="envivo-geolocator-advdb")

    location = None
    intentos = 0
    maxIntentos = 5

    while location is None and intentos < maxIntentos:
        intentos += 1

        try:
            time.sleep(1)
            location = geolocator.geocode(address)
        except GeocoderTimedOut:
            continue

    if location is None:
        raise ValueError("No se pudieron obtener coordenadas")

    return Point((location.longitude, location.latitude))   

class Model:
    """ 
    Clase de modelo abstracta
    Crear tantas clases que hereden de esta clase como  
    colecciones/modelos se deseen tener en la base de datos.

    Attributes
    ----------
        required_vars : set[str]
            conjunto de atributos requeridos por el modelo
        admissible_vars : set[str]
            conjunto de atributos admitidos por el modelo
        db : pymongo.collection.Collection
            conexion a la coleccion de la base de datos
    
    Methods
    -------
        __setattr__(name: str, value: str | dict) -> None
            Sobreescribe el metodo de asignacion de valores a los 
            atributos del objeto con el fin de controlar qué atributos 
            son modificados y cuando son modificados.
        __getattr__(name: str) -> Any
            Sobreescribe el metodo de acceso a atributos del objeto 
        save()  -> None
            Guarda el modelo en la base de datos
        delete() -> None
            Elimina el modelo de la base de datos
        find(filter: dict[str, str | dict]) -> ModelCursor
            Realiza una consulta de lectura en la BBDD.
            Devuelve un cursor de modelos ModelCursor
        aggregate(pipeline: list[dict]) -> pymongo.command_cursor.CommandCursor
            Devuelve el resultado de una consulta aggregate.
        find_by_id(id: str) -> dict | None
            Busca un documento por su id utilizando la cache y lo devuelve.
            Si no se encuentra el documento, devuelve None.
        init_class(db_collection: pymongo.collection.Collection, required_vars: set[str], admissible_vars: set[str]) -> None
            Inicializa las variables de clase en la inicializacion del sistema.

    """
    _required_vars: set[str]
    _admissible_vars: set[str]
    _location_var: str | None = None
    _db: pymongo.collection.Collection
    _internal_vars: set[str] = frozenset(('_modified_vars', '_required_vars', '_admissible_vars', '_db', '_data', '_location_var'))

    def __init__(self, **kwargs: dict[str, str | dict | list]) -> None:
        """
        Inicializa el modelo con los valores proporcionados en kwargs
        Comprueba que los valores proporcionados en kwargs son admitidos
        por el modelo y que las atributos requeridos son proporcionadas.

        Parameters
        ----------
            kwargs : dict[str, str | dict]
                diccionario con los valores de las atributos del modelo
        """
        self._data: dict[str, str | dict | list] = {}

        if not self._required_vars.issubset(kwargs):
            raise ValueError("Missing required fields")

        allowed = self._required_vars | self._admissible_vars | {"_id"}

        if self._location_var is not None:
            allowed.add(self._location_var + "_loc")

        if not set(kwargs).issubset(allowed):
            raise ValueError("Unknown fields")

        self._modified_vars = set()

        self._data.update(kwargs)

    def __setattr__(self, name: str, value: str | dict) -> None:
        """ Sobreescribe el metodo de asignacion de valores a los 
        atributos del objeto con el fin de controlar que atributos 
        son modificados y cuando son modificados.
        """
        if name in self._internal_vars:
            super().__setattr__(name, value)
            return

        allowed = self._required_vars | self._admissible_vars
        if name in allowed:
            self._data[name] = value
            self._modified_vars.add(name)
        else:
            raise ValueError(f"Invalid field: {name}")

    def __getattr__(self, name: str) -> Any:
        """ Sobreescribe el metodo de acceso a atributos del objeto
        __getattr__ solo es llamado cuando no encuentra el atributo
        en el objeto 
        """
        if name in self._internal_vars:
            return super().__getattribute__(name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError
        
    def save(self) -> None:
        """
        Guarda el modelo en la base de datos.
        Si no existe, inserta un documento nuevo.
        Si ya existe, actualiza los atributos modificados.
        """
        address_field = self._location_var

        if address_field is not None and address_field in self._data:
            point_field = address_field + "_loc"

            if (
                "_id" not in self._data
                or address_field in self._modified_vars
                or point_field not in self._data
            ):
                self._data[point_field] = getLocationPoint(self._data[address_field])
                self._modified_vars.add(point_field)

        if "_id" not in self._data:
            result = self._db.insert_one(self._data.copy())
            self._data["_id"] = result.inserted_id

        elif self._modified_vars:
            changes = {}
            for name in self._modified_vars:
                changes[name] = self._data[name]

            self._db.update_one(
                {"_id": self._data["_id"]},
                {"$set": changes},
            )

        self._modified_vars.clear()

    def delete(self) -> None:
        """
        Elimina el modelo de la base de datos
        """
        if "_id" in self._data:
            self._db.delete_one({"_id": self._data["_id"]})
    
    @classmethod
    def find(cls, filter: dict[str, str | dict]) -> Any:
        """ 
        Utiliza el metodo find de pymongo para realizar una consulta
        de lectura en la BBDD.
        find debe devolver un cursor de modelos ModelCursor

        Parameters
        ----------
            filter : dict[str, str | dict]
                diccionario con el criterio de busqueda de la consulta
        Returns
        -------
            ModelCursor
                cursor de modelos
        """ 
        return ModelCursor(cls, cls._db.find(filter))

    @classmethod
    def aggregate(cls, pipeline: list[dict]) -> pymongo.command_cursor.CommandCursor:
        """ 
        Devuelve el resultado de una consulta aggregate. 
        No hay nada que hacer en esta funcion.
        Se utilizara para las consultas solicitadas
        en el segundo proyecto de la practica.

        Parameters
        ----------
            pipeline : list[dict]
                lista de etapas de la consulta aggregate 
        Returns
        -------
            pymongo.command_cursor.CommandCursor
                cursor de pymongo con el resultado de la consulta
        """ 
        return cls._db.aggregate(pipeline)
    
    @classmethod
    def find_by_id(cls, id: str) -> Self | None:
        """ 
        NO IMPLEMENTAR HASTA EL TERCER PROYECTO
        Busca un documento por su id utilizando la cache y lo devuelve.
        Si no se encuentra el documento, devuelve None.

        Parameters
        ----------
            id : str
                id del documento a buscar
        Returns
        -------
            Self | None
                Modelo del documento encontrado o None si no se encuentra
        """ 
        #TODO
        pass

    @classmethod
    def init_class(cls, db_collection: pymongo.collection.Collection, indexes:dict[str,str], required_vars: set[str], admissible_vars: set[str]) -> None:
        """ 
        Inicializa los atributos de clase en la inicializacion del sistema.
        Aqui se deben inicializar o asegurar los indices. Tambien se puede
        alguna otra inicialización/comprobaciones o cambios adicionales
        que estime el alumno.

        Parameters
        ----------
            db_collection : pymongo.collection.Collection
                Conexion a la collecion de la base de datos.
            indexes: Dict[str,str]
                Set de indices y tipo de indices para la coleccion
            required_vars : set[str]
                Set de atributos requeridos por el modelo
            admissible_vars : set[str] 
                Set de atributos admitidos por el modelo
        """
        cls._db = db_collection
        cls._required_vars = required_vars
        cls._admissible_vars = admissible_vars
        cls._location_var = None

        for field, index_type in indexes.items():
            if index_type == "unique":
                cls._db.create_index([(field, pymongo.ASCENDING)], unique=True)
            elif index_type == "asc":
                cls._db.create_index([(field, pymongo.ASCENDING)])
            elif index_type == "geosphere":
                cls._location_var = field;
                cls._db.create_index([(f"{field}_loc", pymongo.GEOSPHERE)])

class ModelCursor:
    """ 
    Cursor para iterar sobre los documentos del resultado de una
    consulta. Los documentos deben ser devueltos en forma de objetos
    modelo.

    Attributes
    ----------
        model_class : Model
            Clase para crear los modelos de los documentos que se iteran.
        cursor : pymongo.cursor.Cursor
            Cursor de pymongo a iterar

    Methods
    -------
        __iter__() -> Generator
            Devuelve un iterador que recorre los elementos del cursor
            y devuelve los documentos en forma de objetos modelo.
    """

    def __init__(self, model_class: Model, cursor: pymongo.cursor.Cursor):
        """
        Inicializa el cursor con la clase de modelo y el cursor de pymongo

        Parameters
        ----------
            model_class : Model
                Clase para crear los modelos de los documentos que se iteran.
            cursor: pymongo.cursor.Cursor
                Cursor de pymongo a iterar
        """
        self.model = model_class
        self.cursor = cursor
    
    def __iter__(self) -> Generator:
        """
        Devuelve un iterador que recorre los elementos del cursor
        y devuelve los documentos en forma de objetos modelo.
        Utilizar yield para generar el iterador
        Utilizar la funcion next para obtener el siguiente documento del cursor
        Utilizar alive para comprobar si existen mas documentos.
        """
        while self.cursor.alive:
            try:
                document = self.cursor.next()
            except StopIteration:
                return

            yield self.model(**document)


def initApp(definitions_path: str = "./models.yml", mongodb_uri="mongodb://localhost:27017/", db_name="abd", scope=globals()) -> None:
    """ 
    Declara las clases que heredan de Model para cada uno de los 
    modelos de las colecciones definidas en definitions_path.
    Inicializa las clases de los modelos proporcionando los indices y 
    atributos admitidos y requeridos para cada una de ellas y la conexión a la
    collecion de la base de datos.
    
    Parameters
    ----------
        definitions_path : str
            ruta al fichero de definiciones de modelos
        mongodb_uri : str
            uri de conexion a la base de datos
        db_name : str
            nombre de la base de datos
    """
    client = MongoClient(mongodb_uri)
    db = client[db_name]

    with open(definitions_path, "r", encoding="utf-8") as definitions_file:
        models = yaml.safe_load(definitions_file)

        for model_name, definition in models.items():
            scope[model_name] = type(model_name, (Model,),{})

            indexes = {}
            for index in definition["unique_indexes"]:
                indexes[index] = "unique"
            for index in definition["regular_indexes"]:
                indexes[index] = "asc"

            if "location_index" in definition:
                indexes[definition["location_index"]] = "geosphere"

            scope[model_name].init_class(db[model_name], indexes, set(definition["required_vars"]), set(definition["admissible_vars"]))

if __name__ == "__main__":
    # Inicializar los modelos.
    initApp()

    # Crear modelo.
    m = Artista(nombre="Artista de prueba", generos=["rock"])

    # Asignar un atributo admitido antes de guardar.
    m.pais = "España"

    # Intentar asignar un atributo no admitido.
    try:
        m.edad = 30
    except ValueError as error:
        print(error)

    # Guardar el documento nuevo.
    m.save()

    # Modificar un atributo admitido.
    m.pais = "Portugal"

    # Guardar los cambios.
    m.save()

    # Buscar el documento.
    resultados = Artista.find({"nombre": "Artista de prueba"})

    # Obtener el primer documento como objeto modelo.
    encontrado = next(iter(resultados))

    # Modificar el objeto recuperado.
    encontrado.pais = "Francia"

    # Guardar los cambios.
    encontrado.save()

    print(encontrado.nombre, encontrado.pais)

    # Borrar el artista
    m.delete()

    recintos = [
        Recinto(nombre="La Riviera", direccion="Madrid, España", aforo=100,
                zonas={"pista": 80, "grada": 20}, servicios=["bar"]),
        Recinto(nombre="Sala Apolo", direccion="Barcelona, España", aforo=200,
                zonas={"pista": 150, "grada": 50}, servicios=["bar", "guardarropa"]),
        Recinto(nombre="La Rambleta", direccion="Valencia, España", aforo=300,
                zonas={"pista": 200, "grada": 100}, servicios=["guardarropa"]),
        Recinto(nombre="Bilborock", direccion="Bilbao, España", aforo=400,
                zonas={"pista": 300, "grada": 100}, servicios=["bar"]),
    ]

    artistas = [
        Artista(nombre="Bad Bunny", generos=["rock"], pais="Puerto Rico", año_inicio=2010),
        Artista(nombre="Aitana", generos=["pop"], pais="España", año_inicio=2015),
        Artista(nombre="Quevedo", generos=["jazz", "folk"], pais="España", año_inicio=2018),
        Artista(nombre="Myke Towers", generos=["rock", "pop"], pais="Puerto Rico", año_inicio=2020),
    ]

    asistentes = [
        Asistente(nombre="Ana Inventada", email="ana@example.com",
                  fecha_registro="2022-01-10", generos_favoritos=["rock"]),
        Asistente(nombre="Luis Inventado", email="luis@example.com",
                  fecha_registro="2023-02-15", generos_favoritos=["pop", "jazz"]),
        Asistente(nombre="Marta Inventada", email="marta@example.com",
                  fecha_registro="2024-03-20", generos_favoritos=["folk"]),
        Asistente(nombre="Pedro Inventado", email="pedro@example.com",
                  fecha_registro="2025-04-25"),
    ]

    # Reutilizar los documentos existentes al ejecutar el programa otra vez.
    for modelo, registros, campo in [(Recinto, recintos, "nombre"),
                                     (Artista, artistas, "nombre"),
                                     (Asistente, asistentes, "email")]:
        for i, registro in enumerate(registros):
            existentes = list(modelo.find({campo: getattr(registro, campo)}))
            if existentes:
                registros[i] = existentes[0]
            else:
                registro.save()

    eventos = [
        Evento(titulo="Noche Azul", artistas=[artistas[0]._id, artistas[3]._id],
               recinto=recintos[0]._id, fecha_hora="2023-05-12T20:00:00",
               precio_zona={"pista": 10, "grada": 15}, entradas_vendidas=50),
        Evento(titulo="Noche Violeta", artistas=[artistas[1]._id],
               recinto=recintos[1]._id, fecha_hora="2024-06-15T21:00:00",
               precio_zona={"pista": 15, "grada": 20}, entradas_vendidas=200),
        Evento(titulo="Noche Verde", artistas=[artistas[2]._id],
               recinto=recintos[2]._id, fecha_hora="2025-07-18T19:00:00",
               precio_zona={"pista": 20, "grada": 25}, entradas_vendidas=0),
        Evento(titulo="Noche Roja", artistas=[artistas[3]._id],
               recinto=recintos[3]._id, fecha_hora="2026-08-21T22:00:00",
               precio_zona={"pista": 25, "grada": 30}, entradas_vendidas=150),
    ]
    for evento in eventos:
        if not list(Evento.find({"titulo": evento.titulo})):
            evento.save()

    print("Datos guardados: 4 recintos, 4 artistas, 4 eventos y 4 asistentes.")
