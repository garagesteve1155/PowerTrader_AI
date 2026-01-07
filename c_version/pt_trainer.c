#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "common.h"
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>

// Simplified trainer: writes trainer_status.json and trainer_last_training_time.txt for a coin

int main(int argc, char **argv) {
    char coin[16] = "BTC";
    if (argc > 1) strncpy(coin, argv[1], sizeof(coin)-1);
    coin[sizeof(coin)-1] = '\0';  // ensure null termination

    // create coin folder (BTC uses current directory; others use coin name)
    if (strcmp(coin, "BTC") != 0) {
        struct stat st = {0};
        if (stat(coin, &st) == -1) {
            if (mkdir(coin, 0700) != 0) {
                fprintf(stderr, "Warning: could not create folder %s\n", coin);
            }
        } else {
            chmod(coin, 0700);
        }
    }

    // mark training started
    char status_path[256]; snprintf(status_path, sizeof(status_path), "%s/trainer_status.json", (strcmp(coin,"BTC")==0)?".":coin);
    char ts_path[256]; snprintf(ts_path, sizeof(ts_path), "%s/trainer_last_training_time.txt", (strcmp(coin,"BTC")==0)?".":coin);

    long ts_now = now_ts();
    char st[256];
    snprintf(st, sizeof(st), "{\"coin\": \"%s\", \"state\": \"TRAINING\", \"started_at\": %ld, \"timestamp\": %ld}\n", coin, ts_now, ts_now);
    write_text_file(status_path, st);

    // simulate work
    printf("pt_trainer.c: training %s (simulated) ...\n", coin);
    fflush(stdout);
    for (int i = 0; i < 3; ++i) { sleep(1); printf("."); fflush(stdout); }
    printf("\n");

    // write finished timestamp
    char tsbuf[64];
    long ts = now_ts();
    snprintf(tsbuf, sizeof(tsbuf), "%ld\n", ts);
    write_text_file(ts_path, tsbuf);

    char st2[256];
    snprintf(st2, sizeof(st2), "{\"coin\": \"%s\", \"state\": \"FINISHED\", \"started_at\": %ld, \"finished_at\": %ld, \"timestamp\": %ld}\n", coin, ts, ts, ts);
    write_text_file(status_path, st2);

    printf("pt_trainer.c: finished training %s.\n", coin);
    return 0;
}
