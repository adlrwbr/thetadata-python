"""Module that contains Theta Client class."""
from typing import Optional, Callable
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, date
import socket
from tqdm import tqdm
import pandas as pd

from . import exceptions
from .enums import *
from .parsing import (
    Header,
    TickBody,
    ListBody,
)

_NOT_CONNECTED_MSG = "You must esetablish a connection first."


def _format_strike(strike: float) -> int:
    """Round USD to the nearest tenth of a cent, acceptable by the terminal."""
    return round(strike * 1000)


def _format_date(dt: date) -> str:
    """Format a date obj into a string acceptable by the terminal."""
    return dt.strftime("%Y%m%d")


class ThetaClient:
    """A high-level, blocking client used to fetch market data."""

    def __init__(self, port: int = 11000, timeout: Optional[float] = 10):
        """Construct a client instance to interface with market data.

        :param port: The port number specified in the Theta Terminal config
        :param timeout: The max number of seconds to wait for a response before
            throwing a TimeoutError
        """
        self.port: int = port
        self.timeout = timeout
        self._server: Optional[socket.socket] = None  # None while disconnected

    @contextmanager
    def connect(self):
        """Initiate a connection with the Theta Terminal on `localhost`.

        :raises ConnectionRefusedError: If the connection failed.
        :raises TimeoutError: If the timeout is set and has been reached.
        """
        try:
            self._server = socket.socket()
            self._server.connect(("localhost", self.port))
            self._server.settimeout(self.timeout)
            yield
        finally:
            self._server.close()

    def _recv(self, n_bytes: int, progress_bar: bool = False) -> bytearray:
        """Wait for a response from the Terminal.
        :param n_bytes:       The number of bytes to receive.
        :param progress_bar:  Print a progress bar displaying download progress.
        :return:              A response from the Terminal.
        """
        assert self._server is not None, _NOT_CONNECTED_MSG

        # receive body data in parts
        MAX_PART_SIZE = 4096  # 4 KiB recommended for most machines

        buffer = bytearray(n_bytes)
        bytes_downloaded = 0

        # tqdm disable=True is slow bc it still calls __new__, which takes nearly 4ms
        range_ = range(0, n_bytes, MAX_PART_SIZE)
        iterable = tqdm(range_, desc="Downloading") if progress_bar else range_

        for i in iterable:
            part_size = min(MAX_PART_SIZE, n_bytes - bytes_downloaded)
            bytes_downloaded += part_size
            part = self._server.recv(part_size)
            buffer[i : i + part_size] = part

        assert bytes_downloaded == n_bytes
        return buffer

    def kill(self) -> None:
        """Remotely kill the Terminal process.

        All subsequent requests will timeout until the Terminal is restarted.
        """
        assert self._server is not None, _NOT_CONNECTED_MSG
        kill_msg = f"MSG_CODE={MessageType.KILL.value}\n"
        self._server.sendall(kill_msg.encode("utf-8"))

    def get_hist_option(
        self,
        req: OptionReqType,
        root: str,
        exp: date,
        strike: float,
        right: OptionRight,
        date_range: DateRange,
        progress_bar: bool = False,
    ) -> pd.DataFrame:
        """
        Get historical option data.

        :param req:            The request type.
        :param root:           The root symbol.
        :param exp:            The expiration date. Must be after the start of `date_range`.
        :param strike:         The strike price in USD, rounded to 1/10th of a cent.
        :param right:          The right of an option.
        :param date_range:     The dates to fetch.
        :param progress_bar:   Print a progress bar displaying download progress.

        :return:               The requested data as a pandas DataFrame.
        :raises ResponseError: If the request failed.
        """
        assert self._server is not None, _NOT_CONNECTED_MSG
        # format data
        strike = _format_strike(strike)
        exp_fmt = _format_date(exp)
        start_fmt = _format_date(date_range.start)
        end_fmt = _format_date(date_range.end)

        # send request
        hist_msg = f"MSG_CODE={MessageType.HIST.value}&START_DATE={start_fmt}&END_DATE={end_fmt}&root={root}&exp={exp_fmt}&strike={strike}&right={right.value}&sec={SecType.OPTION.value}&req={req.value}\n"
        self._server.sendall(hist_msg.encode("utf-8"))

        # parse response header
        header_data = self._server.recv(20)
        header: Header = Header.parse(hist_msg, header_data)

        # parse response body
        body_data = self._recv(header.size, progress_bar=progress_bar)
        body: DataFrame = TickBody.parse(hist_msg, header, body_data)
        return body

    # LISTING DATA

    def get_expirations(self, root: str) -> pd.Series:
        """
        Get all option expirations.

        :param root: The root symbol.
        :return: All expirations that Theta Data provides data for (YYYYMMDD).
        :raises ResponseError: If the request failed.
        """
        assert self._server is not None, _NOT_CONNECTED_MSG
        out = f"MSG_CODE={MessageType.ALL_EXPIRATIONS.value}&root={root}\n"
        self._server.send(out.encode("utf-8"))
        header = Header.parse(out, self._server.recv(20))
        body = ListBody.parse(out, header, self._recv(header.size), dates=True)
        return body.lst

    def get_strikes(self, root: str, exp: date) -> pd.Series:
        """
        Get all option strike prices in US tenths of a cent.

        :param root: The root symbol.
        :param exp: The expiration date.
        :return: The strike prices on the expiration.
        :raises ResponseError: If the request failed.
        """
        assert self._server is not None, _NOT_CONNECTED_MSG
        assert isinstance(exp, date)
        exp_fmt = _format_date(exp)
        out = f"MSG_CODE={MessageType.ALL_STRIKES.value}&root={root}&exp={exp_fmt}\n"
        self._server.send(out.encode("utf-8"))
        header = Header.parse(out, self._server.recv(20))
        body = ListBody.parse(out, header, self._recv(header.size))
        return body.lst

    def get_roots(self, sec: SecType) -> pd.Series:
        """
        Get all roots for a certain security type.

        :param sec: The type of security.
        :return: All root symbols for the security type.
        :raises ResponseError: If the request failed.
        """
        assert self._server is not None, _NOT_CONNECTED_MSG
        out = f"MSG_CODE={MessageType.ALL_ROOTS.value}&sec={sec.value}\n"
        self._server.send(out.encode("utf-8"))
        header = Header.parse(out, self._server.recv(20))
        body = ListBody.parse(out, header, self._recv(header.size))
        return body.lst

    # LIVE DATA

    def get_last_option(
        self,
        req: OptionReqType,
        root: str,
        exp: date,
        strike: float,
        right: OptionRight,
    ) -> pd.DataFrame:
        """
        Get the most recent option data.

        :param req:            The request type.
        :param root:           The root symbol.
        :param exp:            The expiration date.
        :param strike:         The strike price in USD, rounded to 1/10th of a cent.
        :param right:          The right of an option.

        :return:               The requested data as a pandas DataFrame.
        :raises ResponseError: If the request failed.
        """
        assert self._server is not None, _NOT_CONNECTED_MSG
        # format data
        strike = _format_strike(strike)
        exp_fmt = _format_date(exp)

        # send request
        hist_msg = f"MSG_CODE={MessageType.LAST.value}&root={root}&exp={exp_fmt}&strike={strike}&right={right.value}&sec={SecType.OPTION.value}&req={req.value}\n"
        self._server.sendall(hist_msg.encode("utf-8"))

        # parse response
        header: Header = Header.parse(hist_msg, self._server.recv(20))
        body: DataFrame = TickBody.parse(
            hist_msg, header, self._recv(header.size)
        )

        return body
