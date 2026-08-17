"""Fulfilled Academy's pilot roster.

The single source for seeding students, the same way ``curriculum.py`` is for
classes and ``pricing.py`` is for fees: edit the tuples below and re-run
``manage.py seed_students --replace``.

Three things this data is deliberately careful about, because a roster full of
"Student One / Student Two" hides exactly the problems a roster screen exists to
surface:

* **Admission numbers carry the year of admission**, so a Primary 5 child who
  joined in Primary 1 is ``FA/2021/006`` while the one who transferred in this
  September is ``FA/2025/006``. The sequence restarts each year, which is why
  the uniqueness constraint is per branch and per number rather than global.
* **Dates of birth match the class.** A KG 2 child is four; an SSS 3 child is
  sixteen. Ages that do not fit the class make the detail screen look right
  while telling you nothing.
* **Siblings share a parent.** The Okonkwo children sit in Primary 1 and KG 2
  with the same guardian, name, number and address -- the case that will matter
  the moment payments and messaging try to group a family.

Nobody here is a real person. The phone numbers are valid Nigerian mobile
formats on real network prefixes, which is what the roster screens and the
validator need to be exercised honestly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import Sex, StudentStatus

MALE = Sex.MALE
FEMALE = Sex.FEMALE

ACTIVE = StudentStatus.ACTIVE
INACTIVE = StudentStatus.INACTIVE
WITHDRAWN = StudentStatus.WITHDRAWN


@dataclass(frozen=True)
class StudentSpec:
    admission_number: str
    first_name: str
    last_name: str
    other_names: str
    sex: str
    date_of_birth: date
    date_admitted: date
    parent_name: str
    parent_phone: str
    parent_email: str = ""
    address: str = ""
    status: str = ACTIVE


@dataclass(frozen=True)
class ClassRoster:
    """The students to place in one class of the branch."""

    #: Matches ``Class.name``; ``stream`` picks the arm when the year has more
    #: than one.
    class_name: str
    stream: str = ""
    students: tuple[StudentSpec, ...] = ()


def s(
    number: str,
    first: str,
    last: str,
    other: str,
    sex: str,
    born: str,
    admitted: str,
    parent: str,
    phone: str,
    email: str = "",
    address: str = "",
    status: str = ACTIVE,
) -> StudentSpec:
    """Readable shorthand -- dates as ``YYYY-MM-DD`` strings."""
    return StudentSpec(
        admission_number=number,
        first_name=first,
        last_name=last,
        other_names=other,
        sex=sex,
        date_of_birth=date.fromisoformat(born),
        date_admitted=date.fromisoformat(admitted),
        parent_name=parent,
        parent_phone=phone,
        parent_email=email,
        address=address,
        status=status,
    )


# The three September start dates the pilot's students were admitted on.
_2019, _2020, _2021 = "2019-09-09", "2020-09-14", "2021-09-13"
_2022, _2023, _2024, _2025 = "2022-09-12", "2023-09-11", "2024-09-09", "2025-09-08"

# Shared by the two Okonkwo children.
OKONKWO = ("Mrs. Ngozi Okonkwo", "08034129876", "ngozi.okonkwo@example.com",
           "14 Adeniyi Jones Avenue, Ikeja, Lagos")


ROSTERS: tuple[ClassRoster, ...] = (
    # --- Nursery -----------------------------------------------------------
    ClassRoster("KG 2", students=(
        s("FA/2024/006", "Zainab", "Bello", "Amina", FEMALE, "2021-03-25", _2024,
          "Alhaji Musa Bello", "08091142307", "musa.bello@example.com",
          "3 Kayode Street, Ogba, Lagos"),
        s("FA/2024/007", "Tobiloba", "Adeyemi", "Ayomikun", MALE, "2021-05-08", _2024,
          "Mr. Femi Adeyemi", "08160034215", "",
          "27 Awolowo Way, Ikeja, Lagos"),
        s("FA/2024/008", "Chidera", "Okonkwo", "Chukwuemeka", MALE, "2021-07-16", _2024,
          *OKONKWO),
        s("FA/2025/011", "Amara", "Nwachukwu", "", FEMALE, "2021-01-04", _2025,
          "Mrs. Chioma Nwachukwu", "07038825610", "chioma.nwachukwu@example.com",
          "9 Isaac John Street, Gbagada, Lagos"),
        s("FA/2024/009", "Ifeoluwa", "Balogun", "Oluwadamilola", FEMALE, "2021-09-30",
          _2024, "Mrs. Bukola Balogun", "08052276194", "",
          "45 Bode Thomas Street, Surulere, Lagos"),
        s("FA/2025/012", "Emeka", "Obi", "", MALE, "2020-11-22", _2025,
          "Mr. Kelechi Obi", "09062218843", "kelechi.obi@example.com",
          "6 Olusesi Close, Ojodu Berger, Lagos"),
        s("FA/2024/010", "Halima", "Yusuf", "Sadiya", FEMALE, "2021-04-14", _2024,
          "Mrs. Aisha Yusuf", "08131907452", "",
          "18 Ladipo Oluwole Road, Ikeja, Lagos"),
    )),

    # --- Primary -----------------------------------------------------------
    ClassRoster("Primary 1", students=(
        s("FA/2022/002", "Chinaza", "Okonkwo", "Adaeze", FEMALE, "2019-04-12", _2022,
          *OKONKWO),
        s("FA/2025/008", "Daniel", "Ekanem", "Ubong", MALE, "2019-06-18", _2025,
          "Mr. Etim Ekanem", "08023364719", "etim.ekanem@example.com",
          "12 Bourdillon Road, Ikoyi, Lagos"),
        s("FA/2022/003", "Fatima", "Abdullahi", "", FEMALE, "2019-02-09", _2022,
          "Mrs. Hauwa Abdullahi", "08170052283", "",
          "31 Allen Avenue, Ikeja, Lagos"),
        s("FA/2025/009", "Oluwaseun", "Adebayo", "Ayotunde", MALE, "2018-12-27", _2025,
          "Mr. Tunde Adebayo", "08061473920", "tunde.adebayo@example.com",
          "5 Opebi Link Road, Ikeja, Lagos"),
        s("FA/2022/004", "Somtochukwu", "Eze", "Chidinma", FEMALE, "2019-08-03", _2022,
          "Mrs. Adaeze Eze", "07011298364", "",
          "22 Ago Palace Way, Okota, Lagos"),
        s("FA/2025/010", "Miracle", "Udo", "Aniekan", MALE, "2019-05-20", _2025,
          "Mrs. Grace Udo", "09015578204", "grace.udo@example.com",
          "8 Community Road, Akoka, Lagos"),
        s("FA/2022/005", "Aisha", "Sanni", "Omolara", FEMALE, "2019-10-11", _2022,
          "Mr. Ibrahim Sanni", "08087741365", "",
          "40 Randle Avenue, Surulere, Lagos"),
    )),
    ClassRoster("Primary 3", students=(
        s("FA/2023/003", "Ayomide", "Ogunleye", "Oluwatimilehin", MALE, "2017-05-29",
          _2023, "Mr. Segun Ogunleye", "08034420187", "segun.ogunleye@example.com",
          "17 Ogunlana Drive, Surulere, Lagos"),
        s("FA/2023/004", "Chiamaka", "Nwosu", "Ogechi", FEMALE, "2017-03-13", _2023,
          "Mrs. Uchenna Nwosu", "07065583041", "",
          "2 Ikorodu Road, Maryland, Lagos"),
        s("FA/2023/005", "Ibrahim", "Lawal", "Adekunle", MALE, "2016-11-07", _2023,
          "Mallam Sule Lawal", "09031162748", "sule.lawal@example.com",
          "36 Agege Motor Road, Mushin, Lagos"),
        s("FA/2023/006", "Precious", "Etim", "Idara", FEMALE, "2017-07-31", _2023,
          "Mrs. Blessing Etim", "08129940576", "",
          "11 Herbert Macaulay Way, Yaba, Lagos"),
        s("FA/2025/007", "Damilola", "Fashola", "Ifeoluwa", MALE, "2017-01-22", _2025,
          "Mr. Wale Fashola", "08056631409", "wale.fashola@example.com",
          "25 Adeola Odeku Street, Victoria Island, Lagos"),
        s("FA/2023/007", "Nneka", "Anyanwu", "Chidera", FEMALE, "2017-09-14", _2023,
          "Mrs. Ifeoma Anyanwu", "08182207659", "",
          "7 Diya Street, Gbagada, Lagos"),
        s("FA/2023/008", "Kelvin", "Okafor", "Chukwudi", MALE, "2017-02-05", _2023,
          "Mr. Emeka Okafor", "07084416230", "emeka.okafor@example.com",
          "19 Osolo Way, Ajao Estate, Lagos"),
    )),
    ClassRoster("Primary 5", students=(
        s("FA/2021/006", "Temitope", "Alabi", "Oluwaseyi", FEMALE, "2015-04-06", _2021,
          "Mrs. Yemisi Alabi", "08033718264", "yemisi.alabi@example.com",
          "23 Toyin Street, Ikeja, Lagos"),
        s("FA/2021/007", "Uchechukwu", "Madu", "Ekene", MALE, "2015-08-19", _2021,
          "Mr. Obinna Madu", "08147790352", "",
          "4 Oduduwa Crescent, Ikeja GRA, Lagos"),
        s("FA/2025/006", "Rukayat", "Salami", "Adenike", FEMALE, "2015-02-28", _2025,
          "Mrs. Sekinat Salami", "09084432017", "sekinat.salami@example.com",
          "52 Coker Road, Ilupeju, Lagos"),
        s("FA/2021/008", "Ekene", "Iheanacho", "", MALE, "2014-12-11", _2021,
          "Mr. Chidi Iheanacho", "07032268914", "",
          "14 Ejigbo Road, Isolo, Lagos"),
        s("FA/2021/009", "Blessing", "Akpan", "Emem", FEMALE, "2015-06-23", _2021,
          "Mrs. Mfon Akpan", "08051194736", "mfon.akpan@example.com",
          "30 Bariga Road, Bariga, Lagos"),
        s("FA/2022/001", "Olamide", "Shobowale", "Adeoluwa", MALE, "2015-10-02", _2022,
          "Mr. Kunle Shobowale", "08163308471", "",
          "9 Sanusi Fafunwa Street, Victoria Island, Lagos"),
        s("FA/2021/010", "Zara", "Mohammed", "Binta", FEMALE, "2015-01-17", _2021,
          "Mrs. Fatima Mohammed", "09029947158", "fatima.mohammed@example.com",
          "16 Lateef Jakande Road, Agidingbi, Lagos"),
    )),

    # --- Junior secondary --------------------------------------------------
    ClassRoster("JSS 1", students=(
        s("FA/2020/006", "Adaobi", "Chukwu", "Nkechi", FEMALE, "2014-03-19", _2020,
          "Mrs. Chinelo Chukwu", "08036625490", "chinelo.chukwu@example.com",
          "21 Aromire Avenue, Ikeja, Lagos"),
        s("FA/2025/003", "Babajide", "Oyelaran", "Oluwaseun", MALE, "2014-01-08", _2025,
          "Mr. Ademola Oyelaran", "08072215863", "",
          "10 Ilupeju Bypass, Ilupeju, Lagos"),
        s("FA/2020/007", "Hauwa", "Garba", "Zubaida", FEMALE, "2013-11-26", _2020,
          "Alhaji Nuhu Garba", "08118836702", "nuhu.garba@example.com",
          "38 Adeniran Ogunsanya Street, Surulere, Lagos"),
        s("FA/2020/008", "Chukwuemeka", "Nnamdi", "Obiora", MALE, "2014-05-04", _2020,
          "Mr. Ikenna Nnamdi", "07016649238", "",
          "3 Olowu Street, Ikeja, Lagos"),
        s("FA/2025/004", "Funmilayo", "Ajayi", "Oluwakemi", FEMALE, "2014-07-15", _2025,
          "Mrs. Ronke Ajayi", "09053381927", "ronke.ajayi@example.com",
          "48 Ojuelegba Road, Surulere, Lagos"),
        s("FA/2020/009", "Victor", "Ogbonna", "Chinedum", MALE, "2014-09-21", _2020,
          "Mr. Sunday Ogbonna", "08028873164", "",
          "26 Ikotun Road, Egbe, Lagos"),
        s("FA/2025/005", "Maryam", "Idris", "Halimat", FEMALE, "2014-02-14", _2025,
          "Mrs. Rukayat Idris", "08135529740", "rukayat.idris@example.com",
          "13 Oshodi-Apapa Expressway, Oshodi, Lagos"),
    )),
    ClassRoster("JSS 2", students=(
        s("FA/2019/005", "Oluwadamilare", "Sotomi", "Ayodeji", MALE, "2013-04-17", _2019,
          "Mr. Bisi Sotomi", "08039964215", "bisi.sotomi@example.com",
          "5 Emmanuel Keshi Street, Magodo, Lagos"),
        s("FA/2024/002", "Ngozi", "Uzoma", "Chiamaka", FEMALE, "2013-06-02", _2024,
          "Mrs. Ijeoma Uzoma", "07047718360", "",
          "34 Ago Palace Way, Okota, Lagos"),
        s("FA/2024/003", "Abdulrahman", "Bashir", "", MALE, "2012-12-08", _2024,
          "Mallam Yakubu Bashir", "09077264518", "yakubu.bashir@example.com",
          "20 Ipaja Road, Ipaja, Lagos"),
        s("FA/2019/006", "Tamunotonye", "Briggs", "Ibiso", FEMALE, "2013-02-23", _2019,
          "Mrs. Boma Briggs", "08085530146", "boma.briggs@example.com",
          "7 Alexander Avenue, Ikoyi, Lagos"),
        s("FA/2024/004", "Ifeanyi", "Ugochukwu", "Kelechi", MALE, "2013-08-30", _2024,
          "Mr. Nnaemeka Ugochukwu", "08154407923", "",
          "15 Ojota Road, Ojota, Lagos"),
        s("FA/2024/005", "Adaeze", "Okoli", "Chinwe", FEMALE, "2013-05-11", _2024,
          "Mrs. Ebele Okoli", "07091163458", "",
          "2 Alaka Estate, Surulere, Lagos", WITHDRAWN),
    )),

    # --- Senior secondary ---------------------------------------------------
    ClassRoster("SSS 1", "Science", students=(
        s("FA/2021/001", "Boluwatife", "Adesanya", "Oluwatosin", MALE, "2011-05-16",
          _2021, "Mr. Gbenga Adesanya", "08037748012", "gbenga.adesanya@example.com",
          "29 Isheri Road, Omole, Lagos"),
        s("FA/2021/002", "Chisom", "Onyeka", "Amarachi", FEMALE, "2011-03-09", _2021,
          "Mrs. Ijeoma Onyeka", "08106628374", "",
          "11 Ikosi Road, Ketu, Lagos"),
        s("FA/2021/003", "Yusuf", "Aliyu", "Suleiman", MALE, "2010-10-21", _2021,
          "Alhaji Bala Aliyu", "09046691235", "bala.aliyu@example.com",
          "44 Abeokuta Expressway, Abule Egba, Lagos"),
        s("FA/2025/001", "Ememobong", "Essien", "Nsikak", FEMALE, "2011-07-04", _2025,
          "Mrs. Idongesit Essien", "08024417956", "idongesit.essien@example.com",
          "6 Ligali Ayorinde Street, Victoria Island, Lagos"),
        s("FA/2021/004", "Tolulope", "Oshodi", "Adebimpe", MALE, "2011-01-30", _2021,
          "Mr. Yinka Oshodi", "08078834621", "",
          "18 Bank Anthony Way, Ikeja, Lagos"),
        s("FA/2021/005", "Kanyinsola", "Ogunbiyi", "Temiloluwa", FEMALE, "2011-11-12",
          _2021, "Mrs. Adenike Ogunbiyi", "08149920573", "nike.ogunbiyi@example.com",
          "37 Freedom Way, Lekki Phase 1, Lagos"),
        s("FA/2025/002", "Ikechukwu", "Nnaji", "Somtochukwu", MALE, "2010-09-25", _2025,
          "Mr. Chukwuma Nnaji", "07023316840", "",
          "8 Ilaje Road, Bariga, Lagos"),
    )),
    ClassRoster("SSS 2", "Arts", students=(
        s("FA/2020/001", "Folasade", "Odunsi", "Oluwabukola", FEMALE, "2010-02-11",
          _2020, "Mrs. Titilayo Odunsi", "08031127495", "titi.odunsi@example.com",
          "24 Norman Williams Street, Ikoyi, Lagos"),
        s("FA/2020/002", "Osaretin", "Igbinedion", "Efosa", MALE, "2010-06-25", _2020,
          "Mr. Osaze Igbinedion", "09068840317", "",
          "50 Airport Road, Ikeja, Lagos"),
        s("FA/2024/001", "Amina", "Danjuma", "Zahra", FEMALE, "2010-04-03", _2024,
          "Mrs. Salamatu Danjuma", "08115573902", "salamatu.danjuma@example.com",
          "12 Oregun Road, Oregun, Lagos"),
        s("FA/2020/003", "Chinedu", "Agu", "Onyekachi", MALE, "2009-12-19", _2020,
          "Mr. Ifeanyi Agu", "08053396178", "",
          "32 Egbeda Road, Egbeda, Lagos"),
        s("FA/2020/004", "Oluwatobiloba", "Ilesanmi", "Anuoluwapo", FEMALE, "2010-08-08",
          _2020, "Mr. Dare Ilesanmi", "07052248063", "dare.ilesanmi@example.com",
          "3 Olonode Street, Yaba, Lagos"),
        s("FA/2020/005", "Musa", "Tanko", "Abubakar", MALE, "2010-01-27", _2020,
          "Mallam Garba Tanko", "08187762940", "",
          "41 Iju Road, Agege, Lagos", INACTIVE),
    )),
    ClassRoster("SSS 3", "Science", students=(
        s("FA/2019/001", "Adaeze", "Nwafor", "Chinaza", FEMALE, "2009-03-14", _2019,
          "Mrs. Ngozi Nwafor", "08035562817", "ngozi.nwafor@example.com",
          "15 Isaac John Street, Ikeja GRA, Lagos"),
        s("FA/2019/002", "Segun", "Olatunji", "Adewale", MALE, "2009-07-22", _2019,
          "Mr. Kola Olatunji", "08064478139", "",
          "27 Ozumba Mbadiwe Avenue, Victoria Island, Lagos"),
        s("FA/2019/003", "Khadija", "Usman", "Fatima", FEMALE, "2008-11-30", _2019,
          "Alhaji Sani Usman", "09037715264", "sani.usman@example.com",
          "9 Ahmadu Bello Way, Victoria Island, Lagos"),
        s("FA/2023/001", "Chukwudi", "Ezeugo", "Ikenna", MALE, "2009-05-05", _2023,
          "Mr. Obiora Ezeugo", "08126639047", "",
          "22 Okota Road, Okota, Lagos"),
        s("FA/2019/004", "Oyinkansola", "Adeleke", "Motunrayo", FEMALE, "2009-09-18",
          _2019, "Mrs. Folake Adeleke", "08029983412", "folake.adeleke@example.com",
          "6 Karimu Kotun Street, Victoria Island, Lagos"),
        s("FA/2023/002", "Nnamdi", "Okereke", "Chidiebere", MALE, "2008-12-02", _2023,
          "Mr. Emeka Okereke", "07093342785", "",
          "35 Ikorodu Road, Palmgrove, Lagos"),
    )),
)


def total_students() -> int:
    return sum(len(roster.students) for roster in ROSTERS)
