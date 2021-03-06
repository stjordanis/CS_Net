import json
import numpy as np
import os
import random
from tqdm import tqdm

RATIO1 = 0.8
RATIO2 = 0.9

weapon_index_dict = {}

def weapon2index(weapon_list):
    # global w
    global weapon_index_dict

    res = []
    for weapon in weapon_list:
        if weapon in weapon_index_dict:
            res.append(weapon_index_dict[weapon])

    # sort
    res.sort()
    
    return res

def process_data(data):
    # data for each player (10 in total)
    processed_data = {} # [data, label]

    prev_round_score = {}
    for round in range(2, 31):
        if str(round) not in data:
            if round == 2:
                return None
            break

        if round != 2 and len(processed_data) != 10:
            return None

        round_valid = True
        round_data = {}
        

        teams = data[str(round)]["teams"]
        for _, team in teams.items():
            players = team["players"]
            for _, player in players.items():
                player_name = player["player_name"]
                if round == 2:
                    processed_data[player_name] = []

                if player["team_number"] is None:
                    return None

                is_terrorist = int(player["team_number"]) == 2

                round_start = player["round_start"]
                if round_start["weapons"] is None:
                    round_valid = False
                    continue
                weapon_start = round_start["weapons"].split(',')
                if round_start["has_defuser"]:
                    weapon_start.append("defuser")
                if round_start["armor"] > 0:
                    if round_start["has_helmet"]:
                        weapon_start.append("vesthelm")
                    else:
                        weapon_start.append("vest")
                weapon_start = weapon2index(weapon_start)

                if round == 16:
                    continue
                
                # round is not 1 or 16, add round data to result only if data is valid
                player_data = []
                # player's team, 0 for terrorist and 1 for counter terrorist
                player_data.append([0 if is_terrorist else 1])
                # player's weapons at round start
                player_data.append(weapon_start)
                # player's money at round start, divided by 1k for normalization
                player_data.append([int(player["round_start"]["account"]) / 1000])
                # player's performance score at round start, divided by 10*round_num for normalization
                player_score = int(player["round_start"]["player_score"])
                prev_round_score[player_name] = player_score
                player_data.append([player_score / (round * 10)])
                # team vs opponent score
                if data[str(round)]["TvsCT"] is None or not isinstance(data[str(round)]["TvsCT"], str):
                    # data anomaly 
                    round_valid = False
                    continue

                # T VS CT score
                T, CT = data[str(round)]["TvsCT"].split("vs")
                if is_terrorist:
                    player_data.append([int(T) / 15, int(CT) / 15])
                else:
                    player_data.append([int(CT) / 15, int(T) / 15])

                teammate_data = []
                valid = True
                for _, p2 in players.items():
                    if p2["round_start"]["weapons"] is None:
                        # data anomaly 
                        valid = False
                        break
                        
                    weapon_start = p2["round_start"]["weapons"].split(',')
                    if p2["round_start"]["has_defuser"]:
                        weapon_start.append("defuser")
                    if p2["round_start"]["armor"] > 0:
                        if p2["round_start"]["has_helmet"]:
                            weapon_start.append("vesthelm")
                        else:
                            weapon_start.append("vest")
                    teammate_weapons = weapon2index(weapon_start)
                    teammate_money = [int(p2["round_start"]["account"]) / 1000]
                    if p2["round_start"]["player_score"] is None:
                        # data anomaly 
                        valid = False
                        break
                        
                    teammate_score = [int(p2["round_start"]["player_score"]) / (round * 10)]
                    # teammates' money, weapon and score after purchasing
                    teammate_data.append([teammate_weapons, teammate_money, teammate_score])
                
                if not valid:
                    round_valid = False
                    continue
                player_data.append(teammate_data)
                    
                # opponets' data
                valid = True
                opponents_data = []
                for _, t2 in teams.items():
                    for _, p2 in t2["players"].items():
                        if p2["team_number"] is None:
                            valid = False
                            break

                        if int(p2["team_number"]) != int(player["team_number"]):
                            opponent_money = [int(p2["round_start"]["account"]) / 1000]
                            opponent_score = [int(p2["round_start"]["player_score"]) / (round * 10)]
                            # teammates' money score at round start, weapons round start
                            if p2["round_start"]["weapons"] is None:
                                # data anomaly 
                                valid = False
                                break
                            weapon_start = p2["round_start"]["weapons"].split(',')
                            if p2["round_start"]["has_defuser"]:
                                weapon_start.append("defuser")
                            if p2["round_start"]["armor"] > 0:
                                if p2["round_start"]["has_helmet"]:
                                    weapon_start.append("vesthelm")
                                else:
                                    weapon_start.append("vest")
                            opponent_weapons = weapon2index(weapon_start)
                            opponents_data.append([opponent_weapons, opponent_money, opponent_score])

                if not valid:
                    round_valid = False
                    continue
                player_data.append(opponents_data)

                # weapons round_freeze_end
                round_freeze_end = player["round_freeze_end"]
                if round_freeze_end["weapons"] is None:
                    # data anomaly 
                    round_valid = False
                    continue
                weapon_freeze_end = round_freeze_end["weapons"].split(',')
                if round_freeze_end["has_defuser"]:
                    weapon_freeze_end.append("defuser")
                if round_freeze_end["armor"] > 0:
                    if round_freeze_end["has_helmet"]:
                        weapon_freeze_end.append("vesthelm")
                    else:
                        weapon_freeze_end.append("vest")
                weapon_freeze_end = weapon2index(weapon_freeze_end)

                # player's purchasing actions
                pickups = []
                for _, pickup in player["pickup"].items():
                    if pickup["price"] is not None and pickup["price"] > 0:
                        pickups.append(pickup)
                pickups.sort(key=lambda x: x["timestamp"]) 
                
                player_label = []
                for pickup in pickups:
                    for weapon in pickup["equip_names"]:
                        player_label.append(weapon)

                if len(player_label) > 10:
                    # might be a noisy data
                    round_valid = False
                    continue

                player_label = weapon2index(player_label)

                # add data to round_data
                player_score_cur = [player_score - prev_round_score[player_name]]
                round_data[player_name] = [player_data, player_label, weapon_freeze_end, player_score_cur]

        # add data of this round to result
        if round_valid:
            for player_name, r_data in round_data.items():
                processed_data[player_name].append(r_data)
                
    for player_name, p_data in processed_data.items():
        if len(p_data) < 7:
            return None
 
    return processed_data
    
def process_dataset(dataset_dir):
    global weapon_index_dict
    with open("../data/weapon_index.json") as f:
        weapon_index_dict = json.load(f)

    processed_data = []
    for file in tqdm(os.listdir(dataset_dir + "raw/")):
        with open(os.path.join(dataset_dir + "raw/", file)) as f:
            match = json.load(f)
        match_data = process_data(match) # len == 10
        if match_data is None:
            continue
            
        if len(match_data) != 10:
            break

        processed_data.append(match_data)

    random.seed(4164)
    random.shuffle(processed_data)

    train_set = []
    val_set = []
    test_set = []

    total = len(processed_data)
    for i, match_data in enumerate(processed_data):
        md = []
        for _, pd in match_data.items():
            md.append(pd)

        if 0 <= i < int(RATIO1 * total):            
            train_set.append(np.asarray(md))
        elif int(RATIO1 * total) <= i < int(RATIO2 * total):
            val_set.append(np.asarray(md))
        else:
            test_set.append(np.asarray(md))

    print("train set: ", len(train_set), end=" ")
    print("val set: ", len(val_set), end=" ")
    print("test set: ", len(test_set))

    np.save(dataset_dir + "processed.npy", (train_set, val_set, test_set))

def read_dataset(dataset_dir):
    train_set, val_set, test_set = np.load(dataset_dir + "processed.npy", allow_pickle=True)

    print("train set: ", len(train_set), end=" ")
    print("val set: ", len(val_set), end=" ")
    print("test set: ", len(test_set))

    return train_set, val_set, test_set


if __name__ == "__main__":
    dataset_dir = "../data/dataset/"

    process_dataset(dataset_dir)

    # train_set, val_set, test_set = read_dataset(dataset_dir)