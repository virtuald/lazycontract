from __future__ import absolute_import

import re


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class LazyContractError(Exception):
    """ Catch-all exception type """

    pass


class LazyContractValidationError(LazyContractError):
    """ Exception validating data within a LazyContract-derived class """

    INVALID_ATTR_FMT = "no attribute '{}'"
    NOT_NONE_FMT = "{} must not be None"
    REQUIRED_FMT = "'{}' not found in {}"
    ATTR_TYPE_FMT = "value {} is not of type {}"

    FMT = "{}{}: {}"

    def __init__(self, that, path, msg):
        self.clsname = type(that).__name__
        self.path = path
        self.msg = msg

    def __str__(self):
        return self.FMT.format(self.clsname, self.path, self.msg)


class LazyProperty(object):
    """ Base class for descriptors used as properties in a LazyContract.
        Create a sub-class of this to define your own (de-)serialization.
    """

    _type = type(None)  # defines the type that instances take on

    _default_name = "(anonymous)"  # applies to properties within a container

    def __init__(
        self,
        name=None,
        default=None,
        required=False,
        not_none=False,
        exclude_if_none=True,
    ):
        """ Create a LazyProperty.
            name (string):   the attribute name used for (de-)serialization
            default (_type): default value if not available during deserialization
            required (bool): raise LazyContractValidationError if not provided
            not_none (bool): raise LazyContractValidationError if value is None
            exclude_if_none (bool): don't serialize if value is None
        """

        if required and default is not None:
            raise LazyContractError("default specified for required property")

        self.name = name or self._default_name
        self.required = required
        self.default = default
        self.not_none = not_none
        self.exclude_if_none = exclude_if_none

    def __get__(self, obj, objtype=None):
        value = obj.__dict__.get(self.name, self.default)
        self.validate(value)
        return value

    def __set__(self, obj, value):
        self.validate(value)
        obj.__dict__[self.name] = value

    def validate(self, obj):
        """ Validate values whenever they're set.
            Raises LazyContractValidationError.
            Sub-classes that override this probably want to call this method.
        """

        if obj is None and self.not_none:
            raise LazyContractValidationError(
                self, "", LazyContractValidationError.NOT_NONE_FMT.format(self.name)
            )

        if not isinstance(obj, self._type) and obj is not None:
            raise LazyContractValidationError(
                self,
                "." + self.name,
                LazyContractValidationError.ATTR_TYPE_FMT.format(repr(obj), self._type),
            )

    def serialize(self, obj):
        """ Convert the value to a basic serializable type (ex. str)
            Sub-classes probably need to override this.
        """

        return obj

    def deserialize(self, obj):
        """ Convert abasic type to _type.
            Sub-classes probably need to override this.
        """

        return obj if isinstance(obj, self._type) else self._type(obj)


class LazyContract(object):
    """ Base class for custom contracts to be serialized and deserialized. """

    _ignore_undefined_attributes = True

    def __init__(self, _obj=None, **kwargs):
        """ Deserialize a contract.
            _obj (dict): data to deserialize
            kwargs:      individual attributes to deserialize
        """

        if _obj is not None and kwargs:
            raise LazyContractError("both _obj and kwargs provided")

        self._properties = dict()
        self._mappings = dict()

        for cls in reversed(self.__class__.__mro__):
            if cls != LazyContract and issubclass(cls, LazyContract):
                self.__discover_properties(_obj or kwargs, cls)

        self._populate_properties(_obj or kwargs)

    def __repr__(self):
        return "{}({})".format(
            self.__class__.__name__,
            ", ".join(
                "{}={}".format(name, repr(value))
                for name, _, value in self.__iter_properties()
            ),
        )

    def __discover_properties(self, obj, cls):
        if not hasattr(obj, "items"):
            raise LazyContractValidationError(
                self, "", "'{}' is not a dictionary".format(obj)
            )

        for name, inst in cls.__dict__.items():
            if isinstance(inst, LazyProperty):
                self._properties[name] = inst

                if inst.name == inst._default_name:
                    inst.name = name
                else:
                    self._mappings[inst.name] = name

                if inst.name not in obj and name not in obj:
                    if inst.required:
                        raise LazyContractValidationError(
                            self,
                            "",
                            LazyContractValidationError.REQUIRED_FMT.format(
                                inst.name, repr(obj)
                            ),
                        )

                    if inst.not_none and inst.default is None:
                        raise LazyContractValidationError(
                            self,
                            "",
                            LazyContractValidationError.NOT_NONE_FMT.format(inst.name),
                        )

    def _populate_properties(self, obj):
        if not hasattr(obj, "items"):
            raise LazyContractValidationError(
                self, "", "'{}' is not a dictionary".format(obj)
            )

        for key, value in obj.items():
            if key in self._mappings:
                key = self._mappings[key]

            if key not in self._properties:
                if self._ignore_undefined_attributes:
                    continue
                raise LazyContractValidationError(
                    self,
                    "." + key,
                    LazyContractValidationError.INVALID_ATTR_FMT.format(key),
                )

            if value is not None:
                try:
                    value = self._properties[key].deserialize(value)
                except Exception as e:
                    key = "." + key
                    if isinstance(e, LazyContractValidationError):
                        if e.path:
                            key += e.path
                        msg = e.msg
                        context = e.__cause__
                    else:
                        msg = str(e)
                        context = e

                    raise LazyContractValidationError(self, key, msg) from context

            setattr(self, key, value)

    def __iter_properties(self):
        for name, prop in self._properties.items():
            yield name, prop, getattr(self, name)

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            property_pairs = zip(self.__iter_properties(), other.__iter_properties())
            checks = map(lambda x: x[0] == x[1], property_pairs)
            return all(checks)
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def to_dict(self):
        """ Serialize the contract object into a Python dictionary """

        return {
            prop.name: prop.serialize(value)
            for name, prop, value in self.__iter_properties()
            if not prop.name.startswith("_")
            and (value is not None or not prop.exclude_if_none)
        }

    @classmethod
    def contract_properties(cls):
        for name, inst in cls.__dict__.items():
            if isinstance(inst, LazyProperty):
                yield name, inst


class StrictContract(LazyContract):
    """ Variant of LazyContract does not allow undefined attributes
    """

    _ignore_undefined_attributes = False


class DynamicContract(LazyContract):
    """ Variant of LazyContract that deserializes undeclared attributes """

    def _populate_properties(self, obj):
        contained = {
            k: v for k, v in obj.items() if k in self._properties or k in self._mappings
        }
        super(DynamicContract, self)._populate_properties(contained)

        for key, value in obj.items():
            if key not in self._properties and key not in self._mappings:
                setattr(self, key, value)
