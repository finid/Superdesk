'''
Created on Jan 4, 2012

@package: ally core sql alchemy
@copyright: 2012 Sourcefabric o.p.s.
@license: http://www.gnu.org/licenses/gpl-3.0.txt
@author: Gabriel Nistor

Provides support for SQL alchemy mapper that is able to link the alchemy with REST models.
'''

from ally.api.operator.container import Model
from ally.api.operator.descriptor import ContainerSupport, Property, Reference
from ally.api.operator.type import TypeModel, TypeModelProperty
from ally.api.type import typeFor
from ally.container.binder import indexAfter
from ally.container.binder_op import INDEX_PROP, validateAutoId, \
    validateRequired, validateMaxLength, validateProperty, validateManaged
from ally.exception import Ref
from ally.internationalization import _
from ally.support.sqlalchemy.mapper_descriptor import MappedSupport, \
    MappedReference, MappedProperty
from ally.support.sqlalchemy.session import openSession
from ally.support.util import UnextendableMeta
from functools import partial
from inspect import isclass
from sqlalchemy import event
from sqlalchemy.orm import mapper
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.properties import ColumnProperty
from sqlalchemy.schema import Table, MetaData, Column, ForeignKey
from sqlalchemy.sql.expression import Join
from sqlalchemy.types import String

# --------------------------------------------------------------------

INDEX_PROP_FK = indexAfter('propFk', INDEX_PROP)
# Index for foreign key properties

# --------------------------------------------------------------------

class MappingError(Exception):
    '''
    Provides the exception used whenever a mapping issue occurs.
    '''

# --------------------------------------------------------------------

def mapperSimple(clazz, sql, **keyargs):
    '''
    Maps a table to a ally REST model. Use this instead of the classical SQL alchemy mapper since this will
    also provide to the model additional information extracted from the SQL alchemy configurations.
    
    @param clazz: class
        The model class to be mapped with the provided sql table.
    @param sql: Table|Join|Select
        The table or join to map the model with.
    @param keyargs: key arguments
        This key arguments are directly delivered to the SQL alchemy @see mapper.
    @return: class
        The obtained mapped class.
    '''
    assert isclass(clazz), 'Invalid class %s' % clazz
    typeModel = typeFor(clazz)
    assert isinstance(typeModel, TypeModel), 'Invalid model class %s' % clazz
    model = typeModel.container
    assert isinstance(model, Model)
    if isinstance(sql, Table):
        metadata = sql.metadata
    elif isinstance(sql, Join):
        metadata = keyargs.pop('metadata', None)
        assert metadata is not None, \
        'For a join mapping you need to specify the metadata in the key words arguments \'metadata=?\''
    else:
        raise MappingError('Invalid sql source %s' % sql)
    assert isinstance(metadata, MetaData), 'Invalid metadata %s' % metadata
    # We need to treat the case when a model inherits another, since the provided inherited model class is actually the 
    # mapped class the provided model class will not be seen as inheriting the provided mapped class
    inherits = keyargs.get('inherits')
    if inherits is not None:
        assert isclass(inherits), 'Invalid class %s' % inherits
        if __debug__:
            assert issubclass(inherits, ContainerSupport), 'Invalid model inheritance class %s' % inherits
            assert isinstance(inherits, MappedSupport), 'Invalid inherit class %s, is not mapped' % inherits
            assert issubclass(clazz, inherits._ally_type.baseClass), \
            'Invalid inherited mapped class %s, for model class %s' % (inherits._ally_type.baseClass, clazz)

        basses = (clazz, inherits)
    else:
        basses = (clazz,)

    attributes = {'__module__': clazz.__module__}
    attributes['_ally_listeners'] = {} # Added so the mapped class has binding support
    mapped = type.__new__(UnextendableMeta, clazz.__name__ + '$Mapped', basses, attributes)
    mapped._ally_mapping = mapper(mapped, sql, **keyargs)

    mappedModelType = TypeModel(mapped, model, clazz)
    for prop, typ in model.properties.items():
        propType = propRefType = TypeModelProperty(mappedModelType, prop, typ)
        if isinstance(typ, TypeModel):
            propType = TypeModelProperty(mappedModelType, prop, model.properties[model.propertyId])

        instrumented = getattr(mapped, prop)
        if isinstance(instrumented, InstrumentedAttribute):
            reference = MappedReference(propRefType, instrumented)
            setattr(mapped, prop, MappedProperty(propType, reference, instrumented))
        else:
            setattr(mapped, prop, Property(propType, Reference(propRefType)))

    mapped._ally_type = mappedModelType
    try: metadata.__ally_mappings__.append(mapped)
    except AttributeError: metadata.__ally_mappings__ = [mapped]

    return mapped

def mapperModel(clazz, sql, exclude=None, **keyargs):
    '''
    Maps a table to a ally REST model. Use this instead of the classical SQL alchemy mapper since this will
    also provide to the model additional information extracted from the SQL alchemy configurations. Use
    this mapper to also add validations for updating and inserting on the model.
    
    @param clazz: class
        The model class to be mapped with the provided sql table.
    @param sql: Table|Join|Select
        The table or join to map the model with.
    @param exclude: list[string]|tuple(string)
        A list of column names to be excluded from automatic validation.
    @param keyargs: key arguments
        This key arguments are directly delivered to the SQL alchemy @see mapper.  
    @return: class
        The mapped class, basically a model derived class that also contains the mapping data.
    '''
    mapped = mapperSimple(clazz, sql, **keyargs)
    registerValidation(mapped, exclude=exclude)

    return mapped

# --------------------------------------------------------------------

def registerValidation(mapped, exclude=None):
    '''
    Register to mapped class all the validations that can be performed based on the SQL alchemy mapping.
    
    @param mapped: class
        The mapped model class.
    @param exclude: list[string]|tuple(string)
        A list of column names to be excluded from automatic validation.
    @return: Property
        The property id of the model.
    '''
    assert isclass(mapped), 'Invalid class %s' % mapped
    assert isinstance(mapped, MappedSupport), 'Invalid mapped class %s' % mapped
    typeModel = typeFor(mapped)
    assert isinstance(typeModel, TypeModel), 'Invalid model class %s' % mapped
    assert not exclude or isinstance(exclude, (list, tuple)), 'Invalid exclude %s' % exclude
    model = typeModel.container
    assert isinstance(model, Model)

    properties = set(model.properties)
    for cp in mapped._ally_mapping.iterate_properties:
        assert isinstance(cp, ColumnProperty)
        if cp.key:
            prop = cp.key
            try: properties.remove(prop)
            except KeyError: continue

            if not (exclude and prop in exclude):
                propRef = getattr(mapped, prop)
                column = cp.columns[0]
                assert isinstance(column, Column), 'Invalid column %s' % column
                if __debug__:
                    propType = typeFor(propRef)
                    assert isinstance(propType, TypeModelProperty), 'Invalid property %s with type %s' % (prop, propType)
                    #TODO: Check if the property type is matching the column type
                    #assert propType.isOf(column.type), 'Invalid column type %s for property type %s' % \
                    #(column.type, propType)

                if column.primary_key and column.autoincrement:
                    if prop != model.propertyId:
                        raise MappingError('The primary key is expected to be %s, but got SQL primary key for %s' %
                                           (model.propertyId, prop))
                    validateAutoId(propRef)
                elif not column.nullable:
                    validateRequired(propRef)

                if isinstance(column.type, String) and column.type.length:
                    validateMaxLength(propRef, column.type.length)
                if column.unique:
                    validateProperty(propRef, partial(onPropertyUnique, mapped))
                if column.foreign_keys:
                    for fk in column.foreign_keys:
                        assert isinstance(fk, ForeignKey)
                        try: fkcol = fk.column
                        except AttributeError:
                            raise MappingError('Invalid foreign column for %s, maybe you are not using the meta class'
                                               % prop)
                        validateProperty(propRef, partial(onPropertyForeignKey, mapped, fkcol), index=INDEX_PROP_FK)

    for prop in properties:
        if not (exclude and prop in exclude):
            validateManaged(getattr(mapped, prop))

def mappingsOf(metadata):
    '''
    Provides the mapping dictionary of the provided meta.
    
    @param metadata: MetaData
        The meta to get the mappings for.
    @return: dictionary{class,class}
        A dictionary containing as a key the model API class and as a value the mapping class for the model.
    '''
    assert isinstance(metadata, MetaData), 'Invalid meta data %s' % metadata

    try: mappings = metadata.__ally_mappings__
    except AttributeError: return {}

    return {typeFor(mapped).baseClass:mapped for mapped in mappings}

# --------------------------------------------------------------------

def onPropertyUnique(mapped, prop, obj, errors):
    '''
    Validation of a sql alchemy unique property.
    
    @param mapped: class
        The mapped model class.
    @param prop: string
        The property name to be checked if unique.
    @param obj: object
        The entity to check for the property value.
    @param errors: list[Ref]
        The list of errors.
    '''
    assert isclass(mapped), 'Invalid class %s' % mapped
    assert isinstance(prop, str), 'Invalid property name %s' % prop
    assert obj is not None, 'None is not a valid object'
    assert isinstance(errors, list), 'Invalid errors list %s' % errors

    propRef = getattr(mapped, prop)
    if propRef in obj:
        try:
            db = openSession().query(mapped).filter(propRef == getattr(obj, prop)).one()
        except NoResultFound:
            return
        propId = typeFor(mapped).container.propertyId
        if getattr(obj, propId) != getattr(db, propId):
            errors.append(Ref(_('Already an entry with this value'), ref=propRef))
            return False

def onPropertyForeignKey(mapped, foreignColumn, prop, obj, errors):
    '''
    Validation of a sql alchemy fpreign key property.
    
    @param mapped: class
        The mapped model class.
    @param foreignColumn: Column
        The foreign column used for checking.
    @param prop: string
        The property name tthat contains the foreign key.
    @param obj: object
        The entity to check for the property value.
    @param errors: list[Ref]
        The list of errors.
    '''
    assert isclass(mapped), 'Invalid class %s' % mapped
    assert isinstance(foreignColumn, Column), 'Invalid foreign column %s' % foreignColumn
    assert isinstance(prop, str), 'Invalid property name %s' % prop
    assert obj is not None, 'None is not a valid object'
    assert isinstance(errors, list), 'Invalid errors list %s' % errors

    propRef = getattr(mapped, prop)
    if propRef in obj:
        val = getattr(obj, prop)
        if val is not None:
            count = openSession().query(foreignColumn).filter(foreignColumn == val).count()
            if count == 0:
                errors.append(Ref(_('Unknown foreign id'), ref=propRef))
                return False

# --------------------------------------------------------------------

def addLoadListener(mapped, listener):
    '''
    Adds a load listener that will get notified every time the mapped class entity is loaded.
    
    @param mapped: class
        The model mapped class to add the listener to.
    @param listener: callable(object)
        A function that has to take as parameter the model instance that has been loaded.
    '''
    assert isclass(mapped), 'Invalid class %s' % mapped
    assert callable(listener), 'Invalid listener %s' % listener
    def onLoad(target, *args): listener(target)
    event.listen(mapped, 'load', onLoad)

def addInsertListener(mapped, listener, before=True):
    '''
    Adds an insert listener that will get notified every time the mapped class entity is inserted.
    
    @param mapped: class
        The model mapped class to add the listener to.
    @param listener: callable(object)
        A function that has to take as parameter the model instance that will be or has been inserted.
    @param before: boolean
        If True the listener will be notified before the insert occurs, if False will be notified after.
    '''
    assert isclass(mapped), 'Invalid class %s' % mapped
    assert isinstance(mapped, MappedSupport), 'Invalid mapped class %s' % mapped
    assert callable(listener), 'Invalid listener %s' % listener
    assert isinstance(before, bool), 'Invalid before flag %s' % before
    def onInsert(mapper, conn, target): listener(target)
    if before: event.listen(mapped._ally_mapping, 'before_insert', onInsert)
    else: event.listen(mapped._ally_mapping, 'after_insert', onInsert)

def addUpdateListener(mapped, listener, before=True):
    '''
    Adds an update listener that will get notified every time the mapped class entity is update.
    
    @param mapped: class
        The model mapped class to add the listener to.
    @param listener: callable(object)
        A function that has to take as parameter the model instance that will be or has been update.
    @param before: boolean
        If True the listener will be notified before the update occurs, if False will be notified after.
    '''
    assert isclass(mapped), 'Invalid class %s' % mapped
    assert isinstance(mapped, MappedSupport), 'Invalid mapped class %s' % mapped
    assert callable(listener), 'Invalid listener %s' % listener
    assert isinstance(before, bool), 'Invalid before flag %s' % before
    def onUpdate(mapper, conn, target): listener(target)
    if before: event.listen(mapped._ally_mapping, 'before_update', onUpdate)
    else: event.listen(mapped._ally_mapping, 'after_update', onUpdate)
