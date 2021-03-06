from __future__ import print_function, division

import sys
import click
import math

from db_controller import DB_Controller
from comment import Comment
from exception_helper import ExceptionHelper


class DB_Flair_Frequency:
    ## Literals
    MAIN_DB_TABLE = "comments"
    HOURLY_DB_TABLE = "hourly_flair_frequency"
    HOURLY_COLUMNS = ["flair", "frequency", "percentage", "unique_frequency", "unique_percentage", "time_of"]
    APPEND_ARG = "append"
    NO_FLAIR_STR = "no_flair"

    ## Precomputes the hourly flair frequency of the comments, and stores them
    ## into a new table created with:
    """
    CREATE TABLE hourly_flair_frequency (
        flair text NOT NULL,
        frequency integer NOT NULL,
        percentage real NOT NULL,
        unique_frequency integer NOT NULL,
        unique_percentage real NOT NULL,
        time_of integer NOT NULL,
        PRIMARY KEY (flair, time_of)
    );
    """

    def __init__(self, **kwargs):
        self.static = DB_Flair_Frequency

        ## Init the exception helper
        self.exception_helper = ExceptionHelper(log_time=True, std_stream=sys.stderr)

        ## Init the DB
        kwargs["suppress_greeting"] = True
        self.db_controller = DB_Controller(**kwargs)
        self.db = self.db_controller.db

        ## Check and see if thisTuple should be ran in append mode -- adjust start accordinly
        self.append = kwargs.get(self.static.APPEND_ARG, False)
        if(self.append):
            start = self.get_last_frequency_time(self.static.HOURLY_DB_TABLE)
        else:
            start = self.get_first_time_created(self.static.MAIN_DB_TABLE)

        ## Reconfigure for different time steps
        ## Start getting hour data and inserting into the table
        hour_generator = self.generate_hourly_seconds_range(start, self.get_last_time_created(self.static.MAIN_DB_TABLE))

        previous = next(hour_generator)
        for current in hour_generator:
            raw_flair_frequencies = self.get_flair_frequency_between_epoch(previous, current, self.static.MAIN_DB_TABLE)
            raw_unique_flair_frequencies = self.get_unique_flair_frequency_between_epoch(previous, current, self.static.MAIN_DB_TABLE)

            flair_frequencies = self.build_percentage_from_flair_frequencies(raw_flair_frequencies)
            unique_flair_frequencies = self.build_percentage_from_flair_frequencies(raw_unique_flair_frequencies)

            merged = self.merge_flair_frequencies(flair_frequencies, unique_flair_frequencies)

            self.store_flair_frequencies(previous, merged, self.static.HOURLY_DB_TABLE)

            previous = current


    def get_first_time_created(self, table):
        with self.db.cursor() as cursor:
            raw =    """SELECT time_created FROM {0}
                        ORDER BY time_created ASC LIMIT 1;"""

            try:
                cursor.execute(raw.format(table))
            except Exception as e:
                self.exception_helper.print(e, "Unexpected error when getting most recent time_created row from the database.\n", exit=True)
            else:
                return cursor.fetchone()[0]


    def get_last_time_created(self, table):
        with self.db.cursor() as cursor:
            raw =    """SELECT time_created FROM {0}
                        ORDER BY time_created DESC LIMIT 1;"""

            try:
                cursor.execute(raw.format(table))
            except Exception as e:
                self.exception_helper.print(e, "Unexpected error when getting most recent time_created row from the database.\n", exit=True)
            else:
                return cursor.fetchone()[0]


    def get_last_frequency_time(self, table):
        with self.db.cursor() as cursor:
            raw =    """SELECT time_of FROM {0}
                        ORDER BY time_of DESC LIMIT 1;"""

            try:
                cursor.execute(raw.format(table))
            except Exception as e:
                self.exception_helper.print(e, "Unexpected error when getting most recent time_created row from the database.\n", exit=True)
            else:

                try:
                    return cursor.fetchone()[0]
                except TypeError as e:
                    if(self.append):
                        self.exception_helper.print(e, "TypeError when getting most recent time_of row from the database. Assuming empty table.\n")
                        return 0
                    else:
                        self.exception_helper.print(e, "TypeError when getting most recent time_of row from the database.\n", exit=True)
                except Exception as e:
                    self.exception_helper.print(e, "Unexpected error when getting most recent time_of row from the database.\n", exit=True)



    def generate_hourly_seconds_range(self, start, end):
        ## Get start of next complete hour
        start_mod = start % 3600
        start = start - start_mod + (3600 if start_mod > 0 else 0)

        for index in range(start, end + 1, 3600):
            yield index


    def get_flair_frequency_between_epoch(self, start, end, table):
        with self.db.cursor() as cursor:
            raw =    """SELECT flair, count(flair) as frequency FROM {0}
                        WHERE time_created BETWEEN %s AND %s
                        GROUP BY flair
                        ORDER BY COUNT(flair) DESC;"""

            try:
                cursor.execute(raw.format(table), (start, end))
            except Exception as e:
                self.exception_helper.print(e, "Unexpected error when loading flair frequencies from between two epochs.\n", exit=True)
            else:
                return cursor.fetchall()


    def get_unique_flair_frequency_between_epoch(self, start, end, table):
        with self.db.cursor() as cursor:
            raw =    """SELECT DISTINCT ON (flair) flair, COUNT(flair) as frequency
                        FROM (
                            SELECT DISTINCT ON (author) flair
                            FROM {0}
                            WHERE time_created BETWEEN %s AND %s
                            GROUP BY author, flair
                        ) distinct_authors
                        GROUP BY flair
                        ORDER BY flair ASC, frequency DESC;"""

            try:
                cursor.execute(raw.format(table), (start, end))
            except Exception as e:
                self.exception_helper.print(e, "Unexpected error when loading unqiue flair frequencies from between two epochs.\n", exit=True)
            else:
                return cursor.fetchall()


    def build_percentage_from_flair_frequencies(self, flair_frequencies):
        ## https://code.activestate.com/recipes/578114-round-number-to-specified-number-of-significant-di/
        def round_sigfigs(num, sigfigs):
            if(num != 0):
                return round(num, -int(math.floor(math.log10(abs(num))) - (sigfigs - 1)))
            else:
                return 0

        total = 0
        for flair_frequency in flair_frequencies:
            total += flair_frequency[1]
        
        for index, flair_frequency in enumerate(flair_frequencies):
            ## List conversion to get around tuple-immutability
            flair_frequency_list = list(flair_frequency)
            flair_frequency_list.append(round_sigfigs(flair_frequency[1] / total, 3));
            flair_frequencies[index] = tuple(flair_frequency_list)

        return flair_frequencies


    def merge_flair_frequencies(self, flair_frequencies, unique_flair_frequencies):
        def find_tuple(key, index, tuples):
            counter = 0
            foundTuple = False
            while(counter < len(tuples) and not foundTuple):
                thisTuple = tuples[counter]
                if(thisTuple[index] == key):
                    foundTuple = thisTuple
                counter += 1

            return foundTuple, counter

        for index, flair_frequency in enumerate(flair_frequencies):
            key = flair_frequency[0]

            matched_tuple, tuple_index = find_tuple(key, 0, unique_flair_frequencies)
            ## del unique_flair_frequencies[tuple_index]

            if(matched_tuple):
                flair_frequency_list = list(flair_frequency)
                flair_frequency_list.extend(matched_tuple[1:len(matched_tuple)])    # Slice off first element 'flair'
                flair_frequencies[index] = tuple(flair_frequency_list)
            else:
                del flair_frequencies[index]

        return flair_frequencies


    def store_flair_frequencies(self, epoch, flair_frequencies, table):
        if(len(flair_frequencies) > 0):
            for flair_frequency in flair_frequencies:
                try:
                    self.db_controller.insert_row(self.static.HOURLY_COLUMNS, 
                                                  [flair_frequency[0], flair_frequency[1],
                                                   flair_frequency[2], flair_frequency[3],
                                                   flair_frequency[4], epoch],
                                                  self.static.HOURLY_DB_TABLE)
                except IndexError as e:
                    self.exception_helper.print(e, "IndexError when storing flair_frequency. Skipping.")
        else:
            self.db_controller.insert_row(self.static.HOURLY_COLUMNS,
                                          [self.static.NO_FLAIR_STR, 0, 0, 0, 0, epoch],
                                          self.static.HOURLY_DB_TABLE)


@click.command()
@click.option("--remote", "-r", is_flag=True,
              help="Denotes whether or not the comment frequency mover is accessing the database remotely (using {0} instead of {1})".format(DB_Controller.REMOTE_DB_CFG_NAME, DB_Controller.DB_CFG_NAME))
@click.option("--append", "-a", is_flag=True, help="Choose to only append the most recent comments into the flair frequency table, rather than the whole comments table.")
def main(remote, append):
    kwargs = {"remote": remote, DB_Flair_Frequency.APPEND_ARG: append}

    kwargs["remote"] = True

    DB_Flair_Frequency(**kwargs)


if __name__ == "__main__":
    main()
