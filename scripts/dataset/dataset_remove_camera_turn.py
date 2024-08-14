# %%

dataset_path = "/home/users/shen/habitat-web-baselines/data/datasets/objectnav/objectnav_mp3d_70k/train/content"

import os
import gzip
import json
from multiprocessing import Pool, cpu_count

def process_dataset(dataset):
    dataset_path = "/home/users/shen/habitat-web-baselines/data/datasets/objectnav/objectnav_mp3d_70k/train/content"
    
    with gzip.open(os.path.join(dataset_path, dataset), 'r') as f:  # 4. gzip
        json_bytes = f.read()
    json_str = json_bytes.decode('utf-8')  
    data = json.loads(json_str)

    for ind_ep, episode in enumerate(data["episodes"]):
        cleaned_action_ep = []
        for ind_step, step in enumerate(episode["reference_replay"]):
            if step['action'] not in ["LOOK_UP", "LOOK_DOWN"]:
                step["agent_state"]["sensor_data"] = {
                    "rgb": {
                        "rotation": [
                            0,
                            0,
                            0,
                            1
                        ],
                        "position": [
                            0,
                            1.395,
                            0
                        ]
                    },
                    "semantic": {
                        "rotation": [
                            0,
                            0,
                            0,
                            1
                        ],
                        "position": [
                            0,
                            1.395,
                            0
                        ]
                    }
                }
                cleaned_action_ep.append(step)
   
        data["episodes"][ind_ep]["reference_replay"] = cleaned_action_ep
    
    # If you need to save the processed data back, you can do it here
    # with open(os.path.join(dataset_path, "processed_" + dataset), 'w') as f:
    #    json.dump(data, f)
    with gzip.open("cleaned_dataset/"+ dataset, "w") as f:
        f.write(json_bytes)

    return dataset

if __name__ == "__main__":
    dataset_path =  "/home/users/shen/habitat-web-baselines/data/datasets/objectnav/objectnav_mp3d_70k/train/content"
    dataset_name_list = os.listdir(dataset_path)

    # Determine the number of processes to use
    num_processes = cpu_count()

    # Create a pool of workers
    with Pool(num_processes) as pool:
        # Map the datasets to the process_dataset function
        results = pool.map(process_dataset, dataset_name_list)


# %%
