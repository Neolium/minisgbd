from helpers import *
from settings import *
from exceptions import *

import uuid, pickle, os, time, math

class HeaderPageInfo:
    pages_slots = dict()
    nb_pages_de_donnees = 0

class PageId:
    idx = None
    file_id = None

    def __init__(self, file_id):
        check_file_id(file_id)
        self.file_id = file_id

    def get_file_name(self):
        return mount_file_name(self.file_id)

class Record:
    attributes = [] # Strings only

    def set_values(self, values):
        self.attributes = values

class RelDef:
    file_id = None
    slot_count = None
    rel_schema = None
    record_size = None

    def __init__(self, file_id, rel_schema):
        self.file_id = file_id
        self.rel_schema = rel_schema

class RelSchema:
    '''
        Defines a relation (table) schema

        :name: The relation (table) name

        :columns_types: The type of each column of the relation (table)

        :columns_number: The total amount of columns in this relation (table).
    '''
    name = 'DEFAULT_RELSCHEMA_NAME'
    columns_types = []
    columns_number = 0

    def __init__(self, name, columns_number, columns_types):
        self.name = name
        self.columns_types = columns_types
        self.columns_number = columns_number

    def __str__(self):
        return c(self.name) + ' with columns ' + str(self.columns_types)

class DbDef:
    counter = 0
    relations = []

class PageBitmapInfo:
    '''Deserialized version of the page bitmap'''

    '''
    A list of bytes, either with value 0 or 1 (slot used or not)
    The list length should therefore be exactly the slot count of the page
    The value indice is its position on the page (guess 0 indice won't be used)
    '''
    slots_status = [] # Will be a list of bytes from integers using int.to_bytes

class HeapFile:
    '''
        Using:
            - PageDirectory
            - Unpacked/Bitmap
            - Fixed records size
    '''
    buffer = None
    relation = None

    def __init__(self, relation, buffer):
        self.buffer = buffer
        self.relation = relation

    def insert_record(self, record):
        '''
            Public API to insert a record, used by the GlobalManager.

            Using the BufferManager, gets the PageId of a free page & insert the record (arg) in it.
        '''
        pid = self.get_free_page_id()
        self.insert_record_in_page(record, pid)

    def insert_record_in_page(self, record, pid):
        buffer = self.buffer.get_page(pid)
        pbi = PageBitmapInfo()
        self.read_page_bitmap_info(buffer, pbi)
        for (idx, page_slot) in enumerate(pbi.slots_status):
            # page_slot is an element of slots_status, so a bytes either to 0 or 1
            if not int(page_slot):
                # Position is bytes of bitmap & free page index by each record size bytes
                position = self.relation.record_size * idx + self.relation.slot_count
                self.write_record_in_buffer(record, buffer, position)
                pbi.slots_status[idx] = (1).to_bytes(1, byteorder='big') # Mark as used in bitmap
                self.write_page_bitmap_info(buffer, pbi)
                self.buffer.free_page(pid, True) # Since we inserted a record in a page, it's obviously altered and need persistance on disk
                return

    def read_page_bitmap_info(self, buffer, pbi):
        '''
            Takes the first ```HeapFile.relation.slot_count``` values from the buffer to fill
            the ```PageBitmapInfo.slots_status```
        '''
        check_buffer(buffer)
        pbi.slots_status = buffer[:self.relation.slot_count]

    def write_page_bitmap_info(self, buffer, pbi):
        check_buffer(buffer)
        buffer += pbi.slots_status[self.relation.slot_count]

    def create_header(self):
        '''
            Creates the header page of the heap file using the DiskManager.
            Using the BufferManager, writes ```0``` as the HeapFile is (for now) empty = zero pages.
            
            - The disk file written on is described by ```HeapFile.relation.file_id```
            - Calls the BufferManager to handle page modification
        '''
        pid = self.buffer.disk.add_page(self.relation.file_id)
        page = self.buffer.get_page(pid)
        page.append(0)
        self.buffer.free_page(pid, True)

    def read_header_page_info(self, buffer, hpi):
        '''
            Hydrate the HeaderPageInfo (hpi) with data from memory buffer
        '''
        check_buffer(buffer)
        hpi.pages_slots = dict()
        hpi.nb_pages_de_donnees = buffer[0]
        for p in buffer[1:]:
            (pid, slots) = p.split(DATA_SEP)
            hpi.pages_slots[pid] = slots

    def write_header_page_info(self, buffer, hpi):
        '''
            Loads HPI data into the buffer
        '''
        check_buffer(buffer)
        buffer.append(hpi.nb_pages_de_donnees)
        for (key, value) in hpi.pages_slots.items():
            buffer.append(str(key) + DATA_SEP + str(value))

    def get_header_page_info(self, hpi):
        '''
            Loads header page infos into the HeaderPageInfo passed as a parameter (hydrates)
        '''
        pid = PageId(self.relation.file_id)
        pid.idx = 0
        page = self.buffer.get_page(pid)
        self.read_header_page_info(page, hpi)
        self.buffer.free_page(pid, False)

    def update_header_with_new_data_page(self, pid):
        '''
            Updates the header page informations with the current ```HeapFile.relation.slot_count``` value.

            To call when ```slot_count``` is modified to update the header page.
        '''
        hpid = PageId(self.relation.file_id)
        hpid.idx = 0
        page = self.buffer.get_page(hpid)
        hpi = HeaderPageInfo()
        self.read_header_page_info(page, hpi)
        hpi.pages_slots[pid.idx] = self.relation.slot_count
        self.write_header_page_info(page, hpi)
        self.buffer.free_page(pid, True)

    def update_header_taken_slot(self, pid):
        '''
            Update the header page informations ```pages_slots``` by decrementing the value corresponding to the pid arg.

            To call when a slot is used to update header page.
        '''
        hpid = PageId(self.relation.file_id)
        hpid.idx = 0
        page = self.buffer.get_page(hpid)
        hpi = HeaderPageInfo()
        self.read_header_page_info(page, hpi)
        hpi.pages_slots[pid.idx] -= 1
        self.write_header_page_info(page, hpi)
        self.buffer.free_page(hpid, True)

    def write_record_in_buffer(self, record, buffer, position):
        '''
            FIXME: Can't append data to bytes()
        '''
        check_buffer(buffer)
        data = bytes()
        counter = 0
        for attr in record.attributes:
            column_type = self.relation.rel_schema.columns_types[counter]
            if column_type == int:
                data += int(attr)
            elif column_type == float:
                data += float(attr)
            else:
                data += attr

        buffer[positon] = data

    def add_data_page(self):
        '''
            Using the DiskManager, adds a page on the file described by ```HeapFile.relation.file_id```

            :retur: The corresponding PageId
        '''
        pid = self.buffer.disk.add_page(self.relation.file_id)
        self.update_header_with_new_data_page(pid)
        return pid

    def get_free_page_id(self):
        '''
            Returns the PageId of a free page found based on header page infos.
            Adds a page if not page is free
        '''
        hpi = HeaderPageInfo()
        self.get_header_page_info(hpi)
        pid = PageId(self.relation.file_id)
        # Looking for free slots
        for (key, value) in hpi.pages_slots.items():
            if value > 0:
                pid.idx = key
                return pid
        # If no slot has been found free
        return self.add_data_page()

class DiskManager:
    '''
        Each relation is stored into a dedicated file named Data_x.rf
        where x is an integer >= 0 and is named the ```file_id``` into PageId model
    '''
    
    def create_file(self, file_id):
        '''
            Creates an OS file into the ```settings.DATABASE``` folder.
            The file is named Data_x.rf where x is the ```file_id``` argument

            Raises a MiniFileExistsError exception if a file already exists with the deducted name

            :file_id: The file identifier in Data_<file_id>.rf
            :rtypes: None
        '''
        check_file_id(file_id)
        files = os.listdir(DATABASE)
        file_name = mount_file_name(file_id)
        if file_name in files: raise MiniFileExistsError('File {} already exists'.format(file_name))
        else: open(os.path.join(DATABASE, file_name), 'wb').close()

    def add_page(self, file_id):
        '''
            "Adds" a page to the file specified by the file_id. It actually adds nothing but returns the PageId to use to write on this page.

            In practice, it opens a file in _append binary_ mode & uses ```file.tell()``` to get the last position to write on.

            :file_id: The file identifier to add a page to
            :rtype: PageId with ```file_id``` is the one specified & ```idx``` is the offset of the end of the file.
        '''
        check_file_id(file_id)
        pid = PageId(file_id)
        file_name = mount_file_name(pid.file_id)
        f = open(os.path.join(DATABASE, file_name), 'ab')
        pid.idx = int(f.tell() / PAGE_SIZE)
        f.write(bytes([0 for i in range(PAGE_SIZE)]))
        f.close()
        return pid

    def read_page(self, pid, buffer):
        '''
            Reads a disk file page described by pid arg & loads the content as strings (not bytes) into the buffer.

            :pid: Uses pid to pid.get_file_name() & file.seek(pid.idx) before f.read(PAGE_SIZE)
        '''
        check_buffer(buffer)
        f = open(os.path.join(DATABASE, pid.get_file_name()), 'rb')
        f.seek(pid.idx)
        content = f.read(PAGE_SIZE)
        buffer += [o for o in content.decode().strip('\x00').split(DATA_SEP) if bool(o)]
        f.close()

    def write_page(self, pid, buffer):
        '''
            Writes content of the buffer onto the file corresponding to the pid arg.

            - It writes into the file with id ```pid.file_id```
            - It writes at the page offset ```pid.idx``` using file.seek()
            - ```None``` is replaced by ```''``` (empty string)
            - Columns (i.e buffer values) are separated using the ```settings.DATA_SEP```
        '''
        check_buffer(buffer)
        f = open(os.path.join(DATABASE, pid.get_file_name()), 'rb+')
        f.seek(pid.idx)
        f.write(bytes(DATA_SEP.join([o if o is not None else '' for o in buffer]) + DATA_SEP, 'utf-8'))
        f.close()

class BufferManager:

    F = 2
    disk = DiskManager()
    pages_states = dict()
    
    def get_lru(self):
        lru_pid = None
        lru_time = None
        for k, v in self.pages_states.items():
            if lru_time is None or lru_time > v['used']:
                lru_time = v['used']
                lru_pid = k

        return lru_pid
    
    def get_page(self, pid):
        # If the PageId is not in memory, we call the DiskManager to read the page from disk
        if pid.idx not in self.pages_states.keys():
            arr = []
            self.disk.read_page(pid, arr)
            
            # If the buffer pool is full, we delete the least recently used (LRU)
            if len(self.pages_states) == self.F:
                lru_pid = self.get_lru()
                del self.pages_states[lru_pid]

            # We save the read page in the buffer pool
            self.pages_states[pid.idx] = {
                'bitmap': bytes(),
                'pin_count': 1,
                'dirty': False,
                'page': arr,
                'used': time.time()
            }

            # We return page content
            return arr

        # If the PageId is already in memory, we just update the frame & return the page content from memory
        else:
            self.pages_states[pid.idx]['pin_count'] += 1
            self.pages_states[pid.idx]['used'] = time.time()
            return self.pages_states[pid.idx]['page']

    def free_page(self, pid, dirty):
        if dirty:
            self.pages_states[pid.idx]['dirty'] = True
        self.pages_states[pid.idx]['pin_count'] -= 1

class GlobalManager:
    dbdef = None
    files = []
    buffer = BufferManager()

    def __init__(self):
        self.dbdef = DbDef()
        try:
            with open(os.path.join(DATABASE, 'Catalog.def'), 'rb') as obj:
                self.dbdef = pickle.load(obj)
        except Exception:
            pass
        
        self.refresh_heap_files()

    def refresh_heap_files(self):
        for rel_def in self.dbdef.relations:
            self.files.append(HeapFile(rel_def, self.buffer))

    def finish(self):
        with open(os.path.join(DATABASE, 'Catalog.def'), 'wb') as output:
            pickle.dump(self.dbdef, output, pickle.HIGHEST_PROTOCOL)

    def calculate_record_size(columns_types):
        count = 0
        for column in columns_types:
            if column == 'int' or column == 'float':
                count += 4
            elif column[:6] == 'string':
                try:
                    count += int(column[6:])
                except Exception:
                    raise MiniColumnTypeError('Type {} is not correct'.format(column))
            else:
                raise MiniColumnTypeError('Type {} is not correct'.format(column))
        return count

    def calculate_slot_count(record_size, page_size):
        return math.floor(page_size / (record_size + 1))        

    def create_relation(self, name, columns_number, columns_types):
        rel_schema = RelSchema(name, columns_number, columns_types)
        rel_def = RelDef(self.dbdef.counter, rel_schema)
        rel_def.record_size = GlobalManager.calculate_record_size(columns_types)
        rel_def.slot_count = GlobalManager.calculate_slot_count(rel_def.record_size, PAGE_SIZE)
        self.dbdef.relations.append(rel_def)
        self.dbdef.counter += 1
        self.buffer.disk.create_file(rel_def.file_id)
        hf = HeapFile(rel_def, self.buffer)
        hf.create_header()
        self.files.append(hf)

    def insert(self, relation, values):
        rec = Record()
        rec.set_values(values)
        for idx, val in enumerate(self.dbdef.relations):
            if relation == val.rel_schema.name:
                hf = self.files[idx]
                hf.insert_record(rec)
