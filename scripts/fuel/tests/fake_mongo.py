"""Just enough in-memory Mongo for the job tests: equality, $in, $nin, $gt/$gte/$lt/$lte, $ne."""
import copy


def _match_value(value, cond) -> bool:
    if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$in" and value not in arg:
                return False
            if op == "$nin" and value in arg:
                return False
            if op == "$ne" and value == arg:
                return False
            if op in ("$gt", "$gte", "$lt", "$lte"):
                if value is None:
                    return False
                if op == "$gt" and not value > arg:
                    return False
                if op == "$gte" and not value >= arg:
                    return False
                if op == "$lt" and not value < arg:
                    return False
                if op == "$lte" and not value <= arg:
                    return False
        return True
    return value == cond


def matches(doc: dict, query: dict) -> bool:
    return all(_match_value(doc.get(key), cond) for key, cond in query.items())


class FakeCollection:
    def __init__(self):
        self.docs: dict = {}

    def create_index(self, *args, **kwargs):
        return "ok"

    def find(self, query=None, projection=None, sort=None):
        return [copy.deepcopy(d) for d in self.docs.values() if matches(d, query or {})]

    def find_one(self, query=None, projection=None, sort=None):
        rows = self.find(query)
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return rows[0] if rows else None

    def count_documents(self, query):
        return len(self.find(query))

    def distinct(self, key, query=None):
        return sorted({d.get(key) for d in self.find(query)} - {None})

    def replace_one(self, query, doc, upsert=False):
        self.docs[doc["_id"]] = copy.deepcopy(doc)

    def bulk_write(self, ops, ordered=True):
        for op in ops:
            self.replace_one(op._filter, op._doc, upsert=op._upsert)

    def update_one(self, query, update, upsert=False):
        doc = next((d for d in self.docs.values() if matches(d, query)), None)
        if doc is None and upsert:
            doc = self.docs.setdefault(query["_id"], {"_id": query["_id"]})
        if doc is not None:
            doc.update(copy.deepcopy(update.get("$set", {})))

    def update_many(self, query, update):
        for d in self.docs.values():
            if matches(d, query):
                d.update(update.get("$set", {}))

    def delete_many(self, query):
        for key in [k for k, d in self.docs.items() if matches(d, query)]:
            del self.docs[key]


class FakeDB(dict):
    def __getitem__(self, name):
        return self.setdefault(name, FakeCollection())


class FakeClient(dict):
    def __getitem__(self, name):
        return self.setdefault(name, FakeDB())
