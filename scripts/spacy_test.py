import argparse
import torch
import json
import os
import re
import spacy
from tqdm import tqdm
from multiprocessing import Process
import math


def split_list_by_n(origin_list, n):
    step = math.ceil(len(origin_list) / n)
    res = []
    for i in range(0, len(origin_list), step):
        res.append(origin_list[i:i + step])
    return res


def remove_punctuation(text: str) -> str:
    punct = ['|', ':', ';', '@', '(', ')', '[', ']', '{', '}', '^', '\\', '/',
             '\'', '\"', '’', '`', '?', '$', '%', '#', '!', '&', '*', '+', ',', '.'
             ]
    for p in punct:
        text = text.replace(p, '')
    return text.strip()


def split_list_by_n(origin_list, n):
    step = math.ceil(len(origin_list) / n)
    res = []
    for i in range(0, len(origin_list), step):
        res.append(origin_list[i:i + step])
    return res


def get_entity_offset(caption, entities):
    offsets = []
    for entity in entities:
        # want no overlays
        found = {(0, 0)}
        try:
            for m in re.finditer(entity, caption):
                if (m.start(), m.end()) not in found:
                    found.add((m.start(), m.end()))
            offsets.append(len(found) - 2)
        except Exception as e:
            raise ValueError("caption:{}, entity:{}".format(caption, entity))
    return offsets


def get_entities(text, nlp):
    doc = nlp(text)
    return [t.text for t in doc.noun_chunks]


def get_all_entity_map(entity_lists):
    res = []
    for entity_list in entity_lists:
        res.extend(entity_list)
    # return res, dict(zip(res, range(len(res))))
    return res


def compute_offset_map(str1, str0):
    res = []
    j = 0
    offset = 0
    try:
        assert len(str0) >= len(str1)
    except Exception as e:
        raise AssertionError(f"str1:{str1}, str0:{str0}")
    for i in range(len(str0)):
        if j < len(str1) and str0[i] == str1[j]:
            j += 1
        else:
            offset += 1
        res.append(offset)
    return res


def analysis_meta_file(in_path, out_path, nlp):
    with open(in_path, "r", encoding='utf-8') as f, open(out_path, 'a', encoding='utf-8') as f2:
        for line in f:
            data = json.loads(line)
            if data['status'] == 'success':
                origin_caption = data["caption"]
                caption = remove_punctuation(origin_caption)
                offset_map = compute_offset_map(caption, origin_caption)
                doc = nlp(caption)
                new_pos = {}
                old_to_new = {}
                for t in doc.noun_chunks:
                    entity = t.text
                    new_pos[entity] = str(t[0].idx + offset_map[t[0].idx])
                    old_to_new[entity] = entity
                groundings = {}
                original_groundings = {}
                for entity in data['groundings']:
                    for pos in data['groundings'][entity]:
                        groundings[entity] = {}
                        original_groundings[entity] = {}
                        groundings[entity][new_pos[pos]] = data['groundings'][entity][pos]
                        original_groundings[entity][new_pos[pos]] = data['original_groundings'][entity][pos]
                data['groundings'] = groundings
                data['original_groundings'] = original_groundings
                f2.write(json.dumps(data) + '\n')
        f2.close()
        f.close()


def analysis_data_file(in_path, out_path, err_path, nlp):
    with open(in_path, "r", encoding='utf-8') as f, open(out_path, 'a', encoding='utf-8') as f2, open(err_path, 'a', encoding='utf-8') as f3:
        count = 0
        captions = []
        datas = []
        for idx, line in enumerate(f):
            print(idx)
            data = json.loads(line)
            if data['status'] == 'success':
                caption = data["caption"]
                captions.append(remove_punctuation(caption))
            datas.append(data)
            count += 1
            if count == 20:
                entity_lists = [get_entities(caption, nlp) for caption in captions]
                entity_offsets = [get_entity_offset(cap, entities) for cap, entities in zip(captions, entity_lists)]
                entity_offset_cont = []
                cur_offset = 0
                for offset_list in entity_offsets:
                    offsets = []
                    for offset in offset_list:
                        cur_offset += offset
                        offsets.append(cur_offset)
                    entity_offset_cont.append(offsets)
                all_entities = get_all_entity_map(entity_lists)
                assert len(entity_lists) == len(entity_offset_cont)
                all_idx = 0
                for i in range(len(entity_lists)):
                    groundings = {}
                    normal = True
                    original_groundings = {}
                    for j in range(len(entity_lists[i])):
                        old_entity = entity_lists[i][j]
                        offset = entity_offset_cont[i][j]
                        if old_entity in datas[i]['groundings']:
                            if all_idx+offset >= len(all_entities):
                                normal = False
                                all_idx += 1
                                continue
                            new_entity = all_entities[all_idx - offset]
                            groundings[new_entity] = datas[i]['groundings'][old_entity]
                            original_groundings[new_entity] = datas[i]['original_groundings'][old_entity]
                        all_idx += 1
                    if normal:
                        datas[i]['groundings'] = groundings
                        datas[i]['original_groundings'] = original_groundings
                        f2.write(json.dumps(datas[i]) + '\n')
                    else:
                        f3.write(json.dumps(datas[i]) + '\n')
                count = 0
                captions = []
                datas = []
    f2.close()
    f3.close()
    f.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--local_rank', type=int, default=0)
    parser.add_argument('--world_size', type=int, default=1)
    parser.add_argument('--rank', type=int, default=0)
    parser.add_argument('--master_addr', type=str, default='')
    parser.add_argument('--master_port', type=int, default=7878)
    args = parser.parse_args()

    torch.multiprocessing.set_start_method('spawn', force=True)

    os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:128'
    torch.cuda.set_device(args.local_rank)

    spacy.require_gpu(args.local_rank)
    input_path = "/nxchinamobile2/shared/jjh/laion115m_grounding"
    output_path = "/nxchinamobile2/shared/jjh/laion115m_grounding_new"
    ids = []
    for dir in os.listdir(input_path):
        ids.extend(os.listdir(os.path.join(input_path, dir)))
    select_ids = split_list_by_n(ids, args.world_size)[args.rank]
    process_list = []
    nlps = [spacy.load("en_core_web_trf") for _ in range(8)]
    nlp_id = 0
    for id_filename in select_ids:
        dir_name = "part-000"+id_filename[:2]
        output_dir_path = os.path.join(output_path, dir_name)
        if not os.path.exists(output_dir_path):
            os.makedirs(output_dir_path)
        meta_dir_path = os.path.join(input_path, dir_name)
        in_file_path = os.path.join(meta_dir_path, id_filename)
        out_file_path = os.path.join(output_dir_path, id_filename)
        p = Process(target=analysis_meta_file, args=(in_file_path, out_file_path, nlps[nlp_id]))
        nlp_id += 1
        p.start()
        process_list.append(p)
        if len(process_list) >= 8:
            for p in process_list:
                p.join()
            process_list = []
            nlp_id = 0
