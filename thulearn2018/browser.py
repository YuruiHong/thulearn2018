import requests, os, sys, re, getpass, json
import tempfile
from . import settings
from . import filemanager
from . import soup
from . import jsonhelper
from . import utils
from bs4 import BeautifulSoup
from collections import OrderedDict
from requests.packages.urllib3.exceptions import InsecureRequestWarning

class Learn():
    def __init__(self):
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
        self.session = requests.Session()
        self.session.headers = settings.headers

        self.fm = filemanager.FileManager()
        self.username, self.password = self.fm.get_user()
        self.path = self.fm.get_path()
        self.local = self.fm.get_local()

        self.soup = soup.Soup()
        self.jh = jsonhelper.JsonHelper()

    def init(self):
        # login and get current sememster
        self.login()
        self.set_semester()

    def set_user(self):
        self.fm.set_user()

    def set_path(self):
        self.fm.set_path()

    def set_local(self):
        self.fm.set_local()
    
    def get_path(self):
        return self.fm.get_path()

    def get_user(self):
        return self.fm.get_user()

    def post(self, url, form={}, csrf=True, headers=None):
        if csrf:
            params = {
                '_csrf': self.session.cookies.get_dict()['XSRF-TOKEN']
            }
            return self.session.post(url, data=form, params=params, verify=False, headers=headers).content
        else:
            self.session.trust_env = False
            return self.session.post(url, data=form, verify = False, headers=headers).content

    def get(self, url, params={}, csrf=True):
        if csrf:
            params.update({
                '_csrf': self.session.cookies.get_dict()['XSRF-TOKEN']
            })
        return self.session.get(url, params=params).content

    def login(self):
        form = { "i_user" : self.username, "i_pass" : self.password }
        content = self.post(settings.login_id_url, form, csrf=False)
        ticket = self.soup.parse_ticket(content)
        self.post(settings.login_url + ticket, csrf=False)

    def set_semester(self):
        content = self.jh.loads(self.get(settings.semester_url))
        self.semester = content["result"]["id"]

    #-------------------------------------------------------------------------------------------
    def get_lessons(self, ignore=[]):
        content = self.jh.loads(self.post(settings.lessons_url(self.semester)))
        
        # first sort by lesson name, then sort by teacher name
        lessons = [[x["wlkcid"], x["kcm"], x["jsm"], x["kch"]] for x in content["resultList"] if x["kcm"] not in ignore]
        
        lessons.sort(key=lambda x: (x[1], x[2]))
        
        # create a helper function to determine the folder name
        for i in range(len(lessons)):
            _, kcm, jsm, kch = lessons[i]
            
            # check the previous and next lesson to determine the naming method of the current lesson
            prev_lesson = lessons[i-1] if i-1 >= 0 else None
            next_lesson = lessons[i+1] if i+1 < len(lessons) else None
            
            if (prev_lesson is None or prev_lesson[1] != kcm) and (next_lesson is None or next_lesson[1] != kcm):
                lessons[i].append(kcm)
            
            if (prev_lesson is None or prev_lesson[1] != kcm or prev_lesson[2] != jsm) and (next_lesson is None or next_lesson[1] != kcm or next_lesson[2] != jsm):
                lessons[i].append(f"{kcm}_{jsm}")
            
            lessons[i].append(f"{kcm}_{kch}")

        return lessons

    def init_lessons(self, ignore_list):
        lessons = self.get_lessons(ignore_list)
        
        for i in range(len(lessons)):
            self.fm.mkdirl(self.path + os.sep + lessons[i][4])
        return lessons

    def get_files_id(self, lesson_id):
        form = {"wlkcid": lesson_id}
        files = self.jh.loads(self.get(settings.files_url, params=form))
        files_id = [row["id"] for row in files["object"]["rows"] ]

        return files_id

    def file_id_exist(self, fid):
        return (fid in self.local)

    def save_file_id(self, fid):
        if (fid not in self.local):
            self.local.add(fid)
            self.fm.append(settings.local_file_path, fid)

    def download_files(self, lesson_id, lesson_name, file_id):
        # file_id example "sjqy_26ef84e7689589e90168990b993830641"
        files = self.jh.loads(self.get(settings.file_url(lesson_id, file_id)))
        for f in files["object"]:
            #  fid example "2007990011_KJ_1548755901_04ee49a1-3a86-4b4e-841a-b5b55e789234_sjqy01-admin"
            fid = f[7]
            if (not self.file_id_exist(fid)):
                self.get(settings.download_before_url(fid))
                fs = self.session.get(settings.download_url(fid), stream=True)
                if 'Content-Disposition' in fs.headers:
                    fname, extension = os.path.splitext(fs.headers["Content-Disposition"][22:-1])
                elif 'ETag' in fs.headers:
                    fname, extension = os.path.splitext(fs.headers['ETag'])
                else:
                    print('not found name')
                    exit(0)
                # fix special character that exists in filename
                real_filename = re.sub(r'[\:\*\?\<\>\|\\/]', '_', f[1])
                fpath = self.path + os.sep + lesson_name + os.sep + "file" + os.sep + real_filename + extension
                self.fm.downloadto(fpath, fs, real_filename + extension, fid)
                self.save_file_id(fid)

    def download_homework(self, lesson_id, lesson_name):
        ddls = []
        for api in settings.homeworks_url(lesson_id):
            for hw in self.jh.loads(self.get(api))["object"]["aaData"]:
                content = self.get(settings.homework_url(lesson_id, hw))
                hw_title, hw_readme = self.soup.parse_homework(content, hw)
                ddls.append((lesson_name, hw_title, hw["jzsjStr"], hw["zt"]))

                hw_dir = self.path + os.sep + lesson_name + os.sep + "homework" + os.sep + re.sub(r"[\:\*\?\<\>\|\\/]+", "_", hw_title)
                self.fm.init_homework(hw, hw_dir, hw_title, hw_readme)

                annex_name, download_url, annex_id = self.soup.parse_annex(content)
                if (annex_name != "NONE" and not self.file_id_exist(annex_id)):
                    annex = self.session.get(download_url, stream=True)
                    self.fm.downloadto(hw_dir + os.sep + annex_name, annex, annex_name, annex_id)
                    self.save_file_id(annex_id)
        return ddls

    def upload(self, homework_id, file_path, message):
        form = settings.upload_form(homework_id, file_path, message)
        self.post(settings.upload_api, form=form, headers=settings.upload_headers)
        print("done")

    def get_ddl(self, lessons):
        ddls = []
        for lesson in lessons:
            ddls += self.download_homework(lesson[0], lesson[4])
        ddls.sort(key = lambda x: x[2])
        return [[ddl[0], ddl[1], ddl[2], utils.time_delta(ddl[2]), ddl[3]] for ddl in ddls]

def main():
    pass

if __name__ == "__main__":
    main()
