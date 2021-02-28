import pandas as pd
import numpy as np
import ipywidgets as widgets
from ipywidgets import interact, fixed
from ipysheet import sheet, cell, calculation


def search_db(con, tab_name, tab_column, keyword):
    """"
    search through a database based on some keyword

    Parameters
    ---------

    con: sqlite3 connection
        sqlite3 connection to a database

    tab_name: str
        name of the table that you searching

    tab_column: str
        name of the column you are searching

    keyword: str
        keyword to search in your column

    Output
    ------

    df: Pandas DataFrame
        dataframe with the search results
    """

    sql_query = "SELECT * from "
    sql_query += tab_name
    sql_query += " WHERE "
    sql_query += tab_name
    sql_query += "."
    sql_query += tab_column
    sql_query += " LIKE '%"
    sql_query += keyword
    sql_query += "%'"

    df = pd.read_sql_query(sql_query, con)

    return df


def menu_select(db_table, con):
    df = pd.read_sql_query("SELECT * FROM %s" % db_table, con)

    def print_df(name, df):
        return df[df['name'].str.lower().str.contains(name.lower())]

    interact(print_df, name=widgets.Text(value='Dry',
                                         placeholder='Type something',
                                         description='%s:' % db_table,
                                         disabled=False),
             df=fixed(df))


def add_row_table(con, table_name, columns, values):
    """"
    add a new row to a table in db

    Parameters
    ---------

    con: sqlite3 connection
        sqlite3 connection to a database

    tab_name: str
        name of the table that you searching

    columns: list
        list of strings for column names you will add

    values: list
        list of values for the columns you will add
    """

    cur = con.cursor()
    sql = 'INSERT INTO %s(%s) VALUES(' % (table_name, ",".join(columns))
    for i in values:
        if type(i) is str:
            sql += "'%s'," % i
        else:
            sql += "%s," % i
    sql = sql[:-1]
    sql += ')'
    cur.execute(sql)
    con.commit()


class BrewBuild(object):
    """
    build a recipe based on some grain bill,
    hop additons and yeast strain.

    Parameters
    ----------

    grain_bill: np.array
        this is an array of size (N,3), where N
        is the number of fermentables in recipie.
        1st column is always the id in the fermentable
        table in sqlite database, the second column
        is the amount in lbs, and the third column describes
        if this is mash (0) or extract (1)

    hop_bill: np.array
        this is an array of size (N,3), where N
        is the number of hops in recipie.
        1st column is always the id in the hops
        table in sqlite database, the second column
        is the amount in oz and the third column
        is when it is added to the boil (in mins)

    yeast: int
        id of the yeast you will be using, based on
        id in yeast table in sqlite database

    target_volume: float
        target volume of the brew in gallons

    boil_volume: float
        volume of wort pre-boil

    mash_temp: float
        temperature of the mash in F

    con: sqlite3 connection
        connection to sqlite database

    boil_time: float
        length of boil (in min). default is 60 min

    mash_efficiency: float
        mash efficiency as a percentage. If None, it will
        be calculated based on mash temp

    style: int
        style id from style table in sqlite database.
        optional param

    mash_volume: float
        volume of water in mash in gallons
    """

    def __init__(self, grain_bill, hop_bill, yeast, target_volume,
                 boil_volume, mash_temp, con, boil_time=60,
                 mash_efficiency=70, style=None, mash_volume=1):
        self.grain_bill = grain_bill
        self.hop_bill = hop_bill
        self.yeast = yeast
        self.target_volume = target_volume
        self.boil_volume = boil_volume
        self.mash_temp = mash_temp
        self.con = con
        self.boil_time = boil_time
        self.mash_efficiency = mash_efficiency
        self.style = style
        self.mash_volume = mash_volume

        self.OG = None
        self.FG = None
        self.color = None
        self.IBU = None
        self.ABV = None

        # create dataframes for each bill
        # doing this will let me change them then
        # before building the recipe
        sql_query = "SELECT * FROM fermentable as f WHERE f.id = "
        for i in range(len(self.grain_bill)):
            sql_query += str(self.grain_bill[i][0])
            if i + 1 < len(self.grain_bill):
                sql_query += " or f.id = "
        self.df_grain_bill = pd.read_sql_query(sql_query, self.con)

        sql_query = "SELECT * FROM yeast as y WHERE y.id = "
        sql_query += str(self.yeast)
        self.df_yeast = pd.read_sql_query(sql_query, self.con)

        sql_query = "SELECT * FROM hop as h WHERE h.id = "
        for i in range(len(self.hop_bill)):
            sql_query += str(self.hop_bill[i][0])
            if i + 1 < len(self.hop_bill):
                sql_query += " or h.id = "
        self.df_hop_bill = pd.read_sql_query(sql_query, self.con)

        if self.style is not None:
            sql_query = "SELECT * FROM style as s WHERE s.id = "
            sql_query += str(self.style)
            self.df_style = pd.read_sql_query(sql_query, self.con)

    def calc_OG(self):
        """
        calculate the original gravity of recipie
        """

        OG_GU = 0

        for i in range(len(self.grain_bill)):
            idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
            if self.grain_bill[i][2] == 0:
                OG_GU += self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (self.mash_efficiency / 100)
            else:
                OG_GU +=  self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46
        OG_GU /= self.target_volume
        OG = OG_GU / 1000 + 1

        return round(OG, 3)

    def calc_FG(self):
        """
        calculate the final gravity of recipie
        """

        if self.OG is None:
            self.OG = self.calc_OG()
        OG_GU = (self.OG - 1) * 1000

        # get yeast attenuation
        yeast_atten = self.df_yeast.loc[0, 'attenuation']

        # get adjusted attenuation based on mash temp
        # NOTE this is really just change in apparent, but
        #      it works for this to just set it here
        # source of this estimate is
        # https://www.homebrewersassociation.org/forum/index.php?topic=28868.0
        yeast_atten_adj = yeast_atten - (self.mash_temp - 153.5) * 1.25

        # calculate how many gravity points will be taken off
        GU = 0
        for i in range(len(self.grain_bill)):
            idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
            if self.grain_bill[i][2] == 0:
                GU += self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (self.mash_efficiency / 100) * (yeast_atten_adj / 100)
            else:
                GU += self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (yeast_atten / 100)
        GU /= self.target_volume

        # get final gravity
        FG_GU = OG_GU - GU
        FG = FG_GU / 1000 + 1

        return round(FG, 3)

    def calc_ABV(self, OG, FG):
        """
        calculate the abv
        """

        # this is the more accurate one for higher abv
        # got this from https://github.com/Brewtarget/brewtarget/issues/48
        # return (76.08 * (OG - FG) / (1.775 - OG)) * (FG / 0.794)
        # this for lower abv
        return round((OG - FG) * 131.25, 2)

    def calc_color(self):
        """
        calculate the SRM color
        """

        # get MCU first
        # source on this calc is
        # http://www.highwoodsbrewing.com/srm-color.php
        MCU = 0.
        for i in range(len(self.grain_bill)):
            idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
            MCU += (self.grain_bill[i][1] * self.df_grain_bill.loc[idx, 'color']) / self.target_volume
        # get SRM
        SRM = 1.4922 * (MCU ** 0.6859)
        return round(SRM, 1)

    def calc_IBU(self):
        """
        calculate the IBU
        """

        # first calculate boil gravity
        BG_GU = 0

        for i in range(len(self.grain_bill)):
            idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
            if self.grain_bill[i][2] == 0:
                BG_GU += self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (self.mash_efficiency / 100)
            else:
                BG_GU +=  self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46
        BG_GU /= self.boil_volume
        BG = BG_GU / 1000 + 1

        # now start adding up the IBUs
        IBU = 0
        for i in range(len(self.hop_bill)):
            idx = self.df_hop_bill.index[self.df_hop_bill['id'] == self.hop_bill[i][0]].to_list()[0]
            # utilization formula comes from
            # http://howtobrew.com/book/section-1/hops/hop-bittering-calculations
            fG = 1.65 * 0.000125 ** (BG - 1)
            fT = (1 - np.exp(-0.04 * self.hop_bill[i][2])) / 4.15
            U = fG * fT
            # IBU formula from designing great beers
            C_grav = 1 + ((BG - 1.050) / 0.2)
            IBU += (self.hop_bill[i][1] * (self.df_hop_bill.loc[idx, 'alpha'] / 100) * U * 7489) / (self.target_volume * C_grav)
        return round(IBU, 1)

    def build_recipe(self, name):
        """
        build the recipe and write it to a csv with name
        """

        self.OG = self.calc_OG()
        self.FG = self.calc_FG()
        self.color = self.calc_color()
        self.IBU = self.calc_IBU()
        self.ABV = self.calc_ABV(self.OG, self.FG)

        BG_GU = 0

        for i in range(len(self.grain_bill)):
            idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
            if self.grain_bill[i][2] == 0:
                BG_GU += self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (self.mash_efficiency / 100)
            else:
                BG_GU +=  self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46
        BG_GU /= self.boil_volume
        BG = BG_GU / 1000 + 1
        self.BG = BG

        with open('recipe_template.csv', 'r') as ft, open(name, 'w') as fw:
            i = 0
            for x in ft:
                i += 1
                line = x.split(',')
                if i >= 3:
                    # add summary table
                    if i == 3:
                        line[1] = str(self.target_volume)
                    if i == 4:
                        line[1] = str(self.boil_volume)
                    if i == 5:
                        line[1] = str(self.boil_time)
                    if i == 6:
                        line[1] = str(self.mash_temp)
                    if i == 7:
                        line[1] = str(self.OG)
                    if i == 8:
                        line[1] = str(self.FG)
                    if i == 9:
                        line[1] = str(self.IBU)
                    if i == 10:
                        line[1] = str(self.color)
                    if i == 11:
                        line[1] = str(self.mash_efficiency)
                    if i == 12:
                        line[1] = str(self.ABV)

                    # add summary table compared to style
                    if i == 7 and self.style is not None:
                        line[2] = str(self.df_style.loc[0, 'og_min']) + '-' + str(self.df_style.loc[0, 'og_max'])
                        if self.OG < self.df_style.loc[0, 'og_min'] or self.OG > self.df_style.loc[0, 'og_max']:
                            line[3] = 'X'
                    if i == 8 and self.style is not None:
                        line[2] = str(self.df_style.loc[0, 'fg_min']) + '-' + str(self.df_style.loc[0, 'fg_max'])
                        if self.FG < self.df_style.loc[0, 'fg_min'] or self.FG > self.df_style.loc[0, 'fg_max']:
                            line[3] = 'X'
                    if i == 9 and self.style is not None:
                        line[2] = str(self.df_style.loc[0, 'ibu_min']) + '-' + str(self.df_style.loc[0, 'ibu_max'])
                        if self.IBU < self.df_style.loc[0, 'ibu_min'] or self.IBU > self.df_style.loc[0, 'ibu_max']:
                            line[3] = 'X'
                    if i == 10 and self.style is not None:
                        line[2] = str(self.df_style.loc[0, 'color_min']) + '-' + str(self.df_style.loc[0, 'color_max'])
                        if self.color < self.df_style.loc[0, 'color_min'] or self.color > self.df_style.loc[0, 'color_max']:
                            line[3] = 'X'
                    if i == 12 and self.style is not None:
                        line[2] = str(self.df_style.loc[0, 'abv_min']) + '-' + str(self.df_style.loc[0, 'abv_max'])
                        if self.ABV < self.df_style.loc[0, 'abv_min'] or self.ABV > self.df_style.loc[0, 'abv_max']:
                            line[3] = 'X'

                    # add fermentables
                    if i - 3 <= len(self.df_grain_bill) - 1:
                        idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i-3][0]].to_list()[0]
                        line[5] = self.df_grain_bill.loc[idx, 'name'].replace(',', '')
                        line[6] = str(self.grain_bill[i-3][1])
                        if self.grain_bill[i-3][2] == 0:
                            line[7] = 'Mash'
                        else:
                            line[7] = 'Extract'
                        if self.grain_bill[i-3][2] == 0:
                            OG_GU = self.grain_bill[i-3][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (self.mash_efficiency / 100)
                        else:
                            OG_GU =  self.grain_bill[i-3][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46
                        line[8] = str(int(round(OG_GU / self.target_volume, 0)))

                    # add hops
                    if i - 3 <= len(self.df_hop_bill) - 1:
                        idx = self.df_hop_bill.index[self.df_hop_bill['id'] == self.hop_bill[i-3][0]].to_list()[0]
                        line[10] = self.df_hop_bill.loc[idx, 'name'].replace(',', '')
                        line[11] = str(self.hop_bill[i-3][1])
                        line[12] = str(self.hop_bill[i-3][2])
                        # utilization formula comes from
                        # http://howtobrew.com/book/section-1/hops/hop-bittering-calculations
                        fG = 1.65 * 0.000125 ** (BG - 1)
                        fT = (1 - np.exp(-0.04 * self.hop_bill[i-3][2])) / 4.15
                        U = fG * fT
                        # IBU formula from designing great beers
                        C_grav = 1 + ((BG - 1.050) / 0.2)
                        IBU = (self.hop_bill[i-3][1] * (self.df_hop_bill.loc[idx, 'alpha'] / 100) * U * 7489) / (self.target_volume * C_grav)
                        line[13] = str(round(IBU, 1))

                    # add yeast
                    if i == 3:
                        line[15] = self.df_yeast.loc[0, 'name'].replace(',', '')
                        line[16] = str(self.df_yeast.loc[0, 'attenuation'])
                        line[17] = str(self.df_yeast.loc[0, 'attenuation'] - (self.mash_temp - 153.5) * 1.25)
                        line[18] = str(self.df_yeast.loc[0, 'min_temperature'] * 9 / 5 + 32)
                        line[19] = str(self.df_yeast.loc[0, 'max_temperature'] * 9 / 5 + 32) + '\n'

                    # add in pre-boil gravity
                    if i == 17:
                        line[1] = str(self.mash_volume)
                    if i == 18:
                        MG_GU = 0
                        weight = 0

                        for j in range(len(self.grain_bill)):
                            idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[j][0]].to_list()[0]
                            if self.grain_bill[j][2] == 0:
                                MG_GU += self.grain_bill[j][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (self.mash_efficiency / 100)
                                weight += self.grain_bill[j][1]
                        # post mash volume adjustment based on loss of 0.125 gal / lb from
                        # https://www.brewersfriend.com/2010/06/12/water-volume-management-in-all-grain-brewing/
                        MG_GU /= (self.mash_volume - 0.125 * weight)
                        MG = MG_GU / 1000 + 1
                        line[1] = str(round(MG, 3))
                    if i == 19:
                        line[1] = str(round(BG, 3))
                    if i == 20:
                        line[1] = str(self.boil_volume - 0.75 * self.boil_time / 60)
                    if i == 21:
                        PB_GU = BG_GU * self.boil_volume / (self.boil_volume - 0.75 * self.boil_time / 60)
                        PB = PB_GU / 1000 + 1
                        line[1] = str(round(PB, 3))
                # write to new file
                fw.write(",".join(line))

    def interactive_sheet(self):
        """
        create interactive sheet to open in notebook

        NOTES
        -----
        right now, this just changes the summary table.
        cant really change the cells for indivdual GU and
        IBUS for fermentable and hops table (not big deal though)
        """

        # create a sheet with all the info from the template
        df = pd.read_csv('recipe_template.csv', names=np.arange(0, 20, 1)).replace(np.nan, '', regex=True)

        sheet1 = sheet(rows=len(df), columns=len(df.columns))
        for i in range(len(df)):
            for j in range(len(df.columns)):
                if df.iloc[i, j] != '':
                    cell(i, j, df.iloc[i, j])
        # add in cells now
        cell_ferms = []
        cell_OG_GU = []
        for i in range(len(self.grain_bill)):
            idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
            cell(i + 2, 5, self.df_grain_bill.loc[idx, 'name'])
            globals()['cell_ferm_%s' % i] = cell(i + 2, 6, self.grain_bill[i][1], background_color = 'yellow')
            cell_ferms.append(globals()['cell_ferm_%s' % i])
            if self.grain_bill[i][2] == 0:
                cell(i + 2, 7, 'Mash')
            else:
                cell(i + 2, 7, 'Extract')
            if self.grain_bill[i][2] == 0:
                OG_GU = self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (self.mash_efficiency / 100)
            else:
                OG_GU =  self.grain_bill[i][1] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46
            globals()['cell_OG_GU_%s' % i] = cell(i + 2, 8, int(round(OG_GU / self.target_volume, 0)))
            cell_OG_GU.append(globals()['cell_OG_GU_%s' % i])

        cell_ams = []
        cell_times = []
        for i in range(len(self.hop_bill)):
            idx = self.df_hop_bill.index[self.df_hop_bill['id'] == self.hop_bill[i][0]].to_list()[0]
            cell(i + 2, 10, self.df_hop_bill.loc[idx, 'name'])
            globals()['cell_ams_%s' % i] = cell(i + 2, 11, self.hop_bill[i][1], background_color = 'yellow')
            cell_ams.append(globals()['cell_ams_%s' % i])
            globals()['cell_times_%s' % i] = cell(i + 2, 12, self.hop_bill[i][2], background_color = 'yellow')
            cell_times.append(globals()['cell_times_%s' % i])
            # utilization formula comes from
            # http://howtobrew.com/book/section-1/hops/hop-bittering-calculations
            fG = 1.65 * 0.000125 ** (self.BG - 1)
            fT = (1 - np.exp(-0.04 * self.hop_bill[i][2])) / 4.15
            U = fG * fT
            # IBU formula from designing great beers
            C_grav = 1 + ((self.BG - 1.050) / 0.2)
            IBU = (self.hop_bill[i][1] * (self.df_hop_bill.loc[idx, 'alpha'] / 100) * U * 7489) / (self.target_volume * C_grav)
            cell(i + 2, 13, round(IBU, 1))

        cell_yeast_name = cell(2, 15, self.df_yeast.loc[0, 'name'])
        cell_yeast_atten = cell(2, 16, self.df_yeast.loc[0, 'attenuation'], background_color = 'yellow')
        cell_yeast_atten_adj = cell(2, 17, self.df_yeast.loc[0, 'attenuation'] - (self.mash_temp - 153.5) * 1.25, background_color = 'red')
        cell_yeast_min_temp = cell(2, 18, self.df_yeast.loc[0, 'min_temperature'] * 9 / 5 + 32)
        cell_yeast_max_temp = cell(2, 19, self.df_yeast.loc[0, 'max_temperature'] * 9 / 5 + 32)

        cell_target_volume = cell(2, 1, self.target_volume, background_color = 'yellow')
        cell_boil_volume = cell(3, 1, self.boil_volume, background_color = 'yellow')
        cell_boil_time = cell(4, 1, self.boil_time)
        cell_mash_temp = cell(5, 1, self.mash_temp, background_color = 'yellow')
        cell_OG = cell(6, 1, self.calc_OG(), background_color = 'red')
        cell_FG = cell(7, 1, self.FG, background_color = 'red')
        cell_IBU = cell(8, 1, self.IBU, background_color = 'red')
        cell_color = cell(9, 1, self.color, background_color = 'red')
        cell_mash_efficiency = cell(10, 1, self.mash_efficiency, background_color = 'yellow')
        cell_ABV =cell(11, 1, self.ABV, background_color = 'red')

        @calculation(inputs=[cell_OG, cell_FG], output=cell_ABV)
        def cell_ABV_calc(OG, FG):
        	return self.calc_ABV(OG, FG)

        @calculation(inputs=cell_ferms + [cell_mash_efficiency, cell_target_volume], output=cell_OG)
        def cell_calc_OG(*args):
            cell_mash_efficiency = args[-2]
            cell_target_volume = args[-1]
            OG_GU = 0

            for i in range(len(self.grain_bill)):
                idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
                if self.grain_bill[i][2] == 0:
                    OG_GU += args[i] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (cell_mash_efficiency / 100)
                else:
                    OG_GU +=  args[i] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46
            OG_GU /= cell_target_volume
            OG = OG_GU / 1000 + 1
            return round(OG, 3)

        @calculation(inputs=[cell_yeast_atten, cell_mash_temp], output=cell_yeast_atten_adj)
        def calc_adj_atten(cell_yeast_atten, cell_mash_temp):
            yeast_atten = cell_yeast_atten
            yeast_atten_adj = yeast_atten - (cell_mash_temp - 153.5) * 1.25
            return yeast_atten_adj

        @calculation(inputs=cell_ferms + [cell_yeast_atten, cell_mash_efficiency, cell_target_volume, cell_mash_temp, cell_OG], output=cell_FG)
        def cell_calc_FG(*args):
            cell_yeast_atten = args[-5]
            cell_mash_efficiency = args[-4]
            cell_target_volume = args[-3]
            cell_mash_temp = args[-2]
            cell_OG = args[-1]

            OG_GU = (cell_OG - 1) * 1000

            yeast_atten = cell_yeast_atten
            yeast_atten_adj = yeast_atten - (cell_mash_temp - 153.5) * 1.25

            FG_GU = 0

            for i in range(len(self.grain_bill)):
                idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
                if self.grain_bill[i][2] == 0:
                    FG_GU += args[i] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (cell_mash_efficiency / 100) * (yeast_atten_adj / 100)
                else:
                    FG_GU +=  args[i] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (yeast_atten / 100)
            FG_GU /= cell_target_volume
            FG_GU = OG_GU - FG_GU
            FG = FG_GU / 1000 + 1
            return round(FG, 3)

        @calculation(inputs=cell_ams + cell_times + cell_ferms + [cell_mash_efficiency, cell_boil_volume, cell_target_volume], output=cell_IBU)
        def cell_calc_IBU(*args):
            cell_mash_efficiency = args[-3]
            cell_boil_volume = args[-2]
            cell_target_volume = args[-1]
            BG_GU = 0

            for i in range(len(self.grain_bill)):
                idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
                if self.grain_bill[i][2] == 0:
                    BG_GU += args[int(i + 2*len(self.hop_bill))] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46 * (cell_mash_efficiency / 100)
                else:
                    BG_GU +=  args[int(i + 2*len(self.hop_bill))] * (self.df_grain_bill.loc[idx, 'yield'] / 100) * 46
            BG_GU /= cell_boil_volume
            BG = BG_GU / 1000 + 1

            # now start adding up the IBUs
            IBU = 0
            for i in range(len(self.hop_bill)):
                idx = self.df_hop_bill.index[self.df_hop_bill['id'] == self.hop_bill[i][0]].to_list()[0]
                # utilization formula comes from
                # http://howtobrew.com/book/section-1/hops/hop-bittering-calculations
                fG = 1.65 * 0.000125 ** (BG - 1)
                fT = (1 - np.exp(-0.04 * args[i + len(self.hop_bill)])) / 4.15
                U = fG * fT
                # IBU formula from designing great beers
                C_grav = 1 + ((BG - 1.050) / 0.2)
                IBU += (args[i] * (self.df_hop_bill.loc[idx, 'alpha'] / 100) * U * 7489) / (cell_target_volume * C_grav)
            return round(IBU, 1)

        @calculation(inputs=cell_ferms + [cell_target_volume], output=cell_color)
        def cell_calc_color(*args):
            cell_target_volume = args[-1]
            MCU = 0.
            for i in range(len(self.grain_bill)):
                idx = self.df_grain_bill.index[self.df_grain_bill['id'] == self.grain_bill[i][0]].to_list()[0]
                MCU += (args[i] * self.df_grain_bill.loc[idx, 'color']) / cell_target_volume
            # get SRM
            SRM = 1.4922 * (MCU ** 0.6859)
            return round(SRM, 1)

        return sheet1
