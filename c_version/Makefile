CC = gcc
CFLAGS = -O2 -Wall

SOURCES = pt_thinker.c pt_trader.c pt_trainer.c hub_console.c
OBJECTS = $(SOURCES:.c=.o)

all: pt_thinker pt_trader pt_trainer hub_console

pt_thinker: pt_thinker.c common.h
	$(CC) $(CFLAGS) -o $@ pt_thinker.c

pt_trader: pt_trader.c common.h
	$(CC) $(CFLAGS) -o $@ pt_trader.c

pt_trainer: pt_trainer.c common.h
	$(CC) $(CFLAGS) -o $@ pt_trainer.c

hub_console: hub_console.c common.h
	$(CC) $(CFLAGS) -o $@ hub_console.c

clean:
	rm -f $(OBJECTS) pt_thinker pt_trader pt_trainer hub_console
