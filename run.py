import torch
import random
from tqdm import tqdm
import os
import argparse
import numpy as np
import sys
import signal
import time
import copy

#from MAML.args import argument_parser
from src.preprocess import read_dataset
from src.model import CsgoModel
from src.utils import *

from tensorboardX import SummaryWriter

DATA_DIR = './data/2600-15156.npy'

action_money = []
money_scaling = 0

def learning_rate_decay(meta_optimizer, meta_lr):
    for param_group in meta_optimizer.param_groups:
        param_group['lr'] = meta_lr

def get_optimizer(args, model, state=None):
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, betas=(0, 0.999))
    if state is not None:
        optimizer.load_state_dict(state)
    return optimizer

def evaluation(model, optimizer, k_shot, val_set, npy_dict, gate):
    losses = []
    accuracies = []
    accuracies_type = []
    bi_accuracies = []
    ecos = []
    #model.eval()
    for i in tqdm(range(len(val_set))):
        val_data_current = val_set[i]
        if val_data_current.shape[1] <= k_shot + 1:
            continue
        #if len(val_data_current[0]) <= k_shot:
        #    print('found data size less than', k_shot)
        #    continue
        # model.eval()
        model_insight = model.clone(npy_dict)
#         model_insight.eval()

        previous_round = []
        previous_score = []

        for iteration in range(1, k_shot+1):

            # Sample minibatch
            data = val_data_current[:,iteration, 0].tolist().copy()
            labels = val_data_current[:, iteration, 1].tolist()

            previous_round.append(val_data_current[:, iteration - 1, 2].tolist())
            p_round = np.transpose(np.array(previous_round)).tolist()

            previous_score.append(val_data_current[:, iteration - 1, 3].tolist())
            p_score = np.transpose(np.array(previous_score), axes=(1,0,2)).tolist()

            for i in range(len(data)):
                data[i].append(p_round[i])
                data[i].append(p_score[i])

            # Forward pass
            prediction = model_insight.forward(data, gate)
            # Get loss
            loss_dict = model_insight.loss(prediction, labels)
            if gate:
                loss = loss_dict['model_loss']
            else:
                loss = loss_dict['real_seq_loss']
            # Backward pass - Update fast net
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
        # target set
        target_acc = list()
        target_acc_type = list()
        target_eco = list()
        target_bi_acc = list()

        for iteration in range(k_shot+1, len(val_data_current[0])):
            if iteration + k_shot == 17:
                continue
#             data, labels = val_data_current[iteration]
            data = val_data_current[:,iteration, 0].tolist().copy()
            labels = val_data_current[:, iteration, 1].tolist()

            previous_round.append(val_data_current[:, iteration - 1, 2].tolist())
            p_round = np.transpose(np.array(previous_round)).tolist()

            previous_score.append(val_data_current[:, iteration - 1, 3].tolist())
            p_score = np.transpose(np.array(previous_score), axes=(1,0,2)).tolist()

            for i in range(len(data)):
                data[i].append(p_round[i])
                data[i].append(p_score[i])


            prediction = model_insight.forward(data, gate)

            # get batch_sized accuracy
            accuracy = get_batched_acc(prediction[0], labels)
            accuracy_type = get_batched_acc_type(prediction[0], labels, model_insight.id2type)
            binary_accuracy = get_batched_binary_acc(prediction[3], labels, model_insight.id2type)
            money_start = [d[2][0] * money_scaling for d in data]
            eco_diff = get_batched_finance_diff(prediction[0], labels, money_start, action_money)
            target_acc.append(accuracy)
            target_acc_type.append(accuracy_type)
            target_eco.append(eco_diff)
            target_bi_acc.append(binary_accuracy)

        accuracies.append(np.mean(target_acc))
        accuracies_type.append(np.mean(target_acc_type, axis=0))

        ecos.append(np.mean(target_eco))
        bi_accuracies.append(np.mean(target_bi_acc, axis=0))
        

    accuracies_type_mean = np.mean(accuracies_type, axis=0)
    acc_gun, acc_grenade, acc_equip = accuracies_type_mean[0], accuracies_type_mean[1], accuracies_type_mean[2]
   
    return np.mean(accuracies), acc_gun, acc_grenade, acc_equip, np.mean(ecos), np.mean(bi_accuracies, axis=0)

def insight_learning(model_insight, optimizer, k_shot, train_data_current, gate):

    model_insight.train()
    # support set
    index = [i for i in range(1, k_shot+1)]

    np.random.shuffle(train_data_current)

#     for i in range(10):
#         print(train_data_current[i,2,1])
#     print()

    previous_round = []
    previous_score = []

    for iteration in index:
        # Sample minibatch
        data = train_data_current[:, iteration, 0].tolist()
        labels = train_data_current[:, iteration, 1].tolist()

        previous_round.append(train_data_current[:, iteration - 1, 2].tolist())
        p_round = np.transpose(np.array(previous_round)).tolist()

        previous_score.append(train_data_current[:, iteration - 1, 3].tolist())
        p_score = np.transpose(np.array(previous_score), axes=(1,0,2)).tolist()


        for i in range(len(data)):
            data[i].append(p_round[i])
            data[i].append(p_score[i])

        # Forward pass
        # x: [[x_self_2,...,x_self_k-1](after purchase), [x_self,x_teammate1, ...](before purchase), [x_opponents1, ...](before purchase),[money_self,money_teammate1,...], ] weapon indexes of all players
        # money: [money_self, [money_teammate1, ...], [money_opponents1, ...]], normalized
        # performance: same as money
        prediction = model_insight.forward(data, gate)

        # Get loss
        loss_dict = model_insight.loss(prediction, labels)# TODO
        if gate:
            loss = loss_dict['model_loss']
        else:
            loss = loss_dict['real_seq_loss']

        # Backward pass - Update fast net
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    # target set
    target_loss = list()
    target_acc = list()
    target_acc_gun = list()
    target_acc_grenade = list()
    target_acc_equip = list()
    target_eco = list()
    target_bi_acc = list()
    # for Tensorboard
    target_seq_loss = list()
    target_bi_loss = list()
    for iteration in range(k_shot+1, len(train_data_current[0])):
        if iteration + k_shot == 17:
            continue
        data = train_data_current[:,iteration,0].tolist().copy()
        labels = train_data_current[:,iteration,1].tolist()

        previous_round.append(train_data_current[:, iteration - 1, 2].tolist())
        p_round = np.transpose(np.array(previous_round)).tolist()

        previous_score.append(train_data_current[:, iteration - 1, 3].tolist())
        p_score = np.transpose(np.array(previous_score), axes=(1,0,2)).tolist()

        for i in range(len(data)):
            data[i].append(p_round[i])
            data[i].append(p_score[i])

        prediction = model_insight.forward(data, gate)
        loss_dict = model_insight.loss(prediction, labels)
        if gate:
            loss = loss_dict['model_loss']
        else:
            loss = loss_dict['real_seq_loss']

        target_loss.append(loss.unsqueeze(0).double())
        accuracy = get_batched_acc(prediction[0], labels)
        acc_type = get_batched_acc_type(prediction[0], labels, model_insight.id2type)
        binary_accuracy = get_batched_binary_acc(prediction[3], labels, model_insight.id2type)
        money_start = [d[2][0] * money_scaling for d in data]
        eco_diff = get_batched_finance_diff(prediction[0], labels, money_start, action_money)
        target_acc.append(accuracy)
        target_acc_gun.append(acc_type[0])
        target_acc_grenade.append(acc_type[1])
        target_acc_equip.append(acc_type[2])
        target_eco.append(eco_diff)
        target_bi_acc.append(binary_accuracy)

        target_seq_loss.append(loss_dict['seq_loss'])
        target_bi_loss.append(loss_dict['bi_loss'])

    # Backward pass - Update fast net
    target_loss = torch.cat(target_loss, -1)
    loss = torch.mean(target_loss)
    accuracy = np.mean(target_acc)
    acc_gun = np.mean(target_acc_gun)
    acc_grenade = np.mean(target_acc_grenade)
    acc_equip = np.mean(target_acc_equip)
    eco = np.mean(target_eco)
    bi_accuracy = np.mean(target_bi_acc, axis=0)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    return np.nanmean(target_seq_loss, axis=0), np.mean(target_bi_loss, axis=0), accuracy, acc_gun, acc_grenade, acc_equip, eco, bi_accuracy


def main():

    """
    Load args
    """
    # Parsing
    parser = argparse.ArgumentParser('Train MAML on CSGO')
    # params
    parser.add_argument('--logdir', default='log/', type=str, help='Folder to store everything/load')
    parser.add_argument('--statedir', default='iteration3-score-weighted', type=str, help='Folder name to store model state')
    parser.add_argument('--player_mode', default='terrorist', type=str, help='terrorist or counter_terrorist')
    parser.add_argument('--shots', default=5, type=int, help='shots per class (K-shot)')
    parser.add_argument('--start_meta_iteration', default=0, type=int, help='start number of meta iterations')
    parser.add_argument('--meta_iterations', default=6000, type=int, help='number of meta iterations')
    parser.add_argument('--meta_lr', default=0.1, type=float, help='meta learning rate')
    parser.add_argument('--lr', default=5e-4, type=float, help='base learning rate')
    parser.add_argument('--validate_every', default=500, type=int, help='validate every')
    parser.add_argument('--check_every', default=500, type=int, help='Checkpoint every')
    parser.add_argument('--checkpoint', default='log/checkpoint', help='Path to checkpoint. This works only if starting fresh (i.e., no checkpoints in logdir)')
    parser.add_argument('--action_embedding', default = '/home/derenlei/MAML/data/action_embedding2.npy', help = 'Path to action embedding.')
    parser.add_argument('--action_name', default = '/home/derenlei/MAML/data/action_name.npy', help = 'Path to action name.')
    parser.add_argument('--action_money', default = '/home/derenlei/MAML/data/action_money.npy', help = 'Path to action money.')
    parser.add_argument('--money_scaling', default =1000, help = 'Scaling factor between money features and actual money.')
    parser.add_argument('--side_mask', default = '/home/derenlei/MAML/data/mask.npz', help = 'Path to mask of two sides.')
    parser.add_argument('--action_capacity', default = '/home/derenlei/MAML/data/action_capacity.npy', help = 'Path to action capacity.')
    parser.add_argument('--id2type', default = '/home/derenlei/MAML/data/action_type.npy', help = 'Path to id2type.')
    parser.add_argument('--type_capacity', default = '/home/derenlei/MAML/data/type_capacity.npy', help = 'Path to type capacity.')
    parser.add_argument('--typeid2name', default = '/home/derenlei/MAML/data/type_name.npy', help = 'Path to typeid2name.')
    parser.add_argument('--history_dim', default = 512, help = 'LSTM hidden dimension.')
    parser.add_argument('--history_num_layers', default = 2, help = 'LSTM layer number.')
    parser.add_argument('--ff_dim', default = 512, help = 'MLP dimension.')
    parser.add_argument('--resource_dim', default = 2, help = 'Resource (money, performance, ...) dimension.')
    parser.add_argument('--ff_dropout_rate', default = 0.1, help = 'Dropout rate of MLP.')
    parser.add_argument('--max_output_num', default = 10, help = 'Maximum number of actions each round.')
    parser.add_argument('--beam_size', default = 128, help = 'Beam size of beam search predicting.')
    parser.add_argument('--seed', default = 4164, help = 'random seed.')
    parser.add_argument('--shared_attention_weight', default = True, help = 'Sharing weight of attention layers or not.')
    parser.add_argument('--different_attention_weight', default = False, help = 'Different weight of attention for history information.')
    parser.add_argument('--history_encoding', default = None, help = "Encode current player's weapon history.")
    parser.add_argument('--time_decaying', default = 0.99, help = "Decay of player's weighted sum history.")
    parser.add_argument('--gate', type=bool, default = True, help = "Use classifier or not.")
    parser.add_argument('--lstm_mode', default='triple', help="Use single or triple lstm.")
    parser.add_argument('--mode', default = 'train', help = "train or test")


    # args Processing
    args = parser.parse_args()
    print(args)

    npy_dict = read_npy(args)

    global money_scaling, action_money
    money_scaling = args.money_scaling
    action_money = npy_dict["action_money"]

    #run_dir = args.logdir
    check_dir = args.logdir + 'checkpoint/' + args.statedir # os.path.join(run_dir, 'checkpoint')

    """
    Load data and construct model
    """
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)
    np.random.seed(args.seed)

    train_set, val_set, test_set = read_dataset(DATA_DIR) # TODO: implement DATA_DIR

    if args.mode == 'test':
        print("Begin test mode")
        # Meta Test
        model = CsgoModel(args, npy_dict)
        print('\nTest Performance')
        checkpoint_path = os.path.join(check_dir, 'best_eval.pth')
        print('Attempting to load checkpoint', checkpoint_path)
        checkpoint = torch.load(checkpoint_path)
        model.load_state_dict(checkpoint['meta_net'])
        state = checkpoint['optimizer']
        model_insight = model.clone(npy_dict)
        optimizer = get_optimizer(args, model_insight, state)

        # Update insight model
        test_accuracy, test_acc_gun, test_acc_grenade, test_acc_equip, test_eco, test_bi_accuracy = evaluation(model_insight, optimizer, args.shots, test_set, npy_dict, args.gate)
        print('average test_accuracy', test_accuracy)
        print('average gun accuracy', test_acc_gun)
        print('average grenade accuracy', test_acc_grenade)
        print('average equipment accuracy', test_acc_equip)
        print('average test_eco', test_eco)
        print('average test_bi_accuracy', test_bi_accuracy)
        sys.exit(0)



    # build model, optimizer
    model = CsgoModel(args, npy_dict) # TODO: add args
    model.print_all_model_parameters()
    if torch.cuda.is_available():
        model.cuda()
    meta_optimizer = torch.optim.SGD(model.parameters(), lr=args.meta_lr) # TODO: add args
    info = {}
    state = None
    meta_iteration = 0

    """
    Load checkpoint
    """
    # checkpoint is directory -> Find last model or '' if does not exist
    print('check_dir', check_dir)
    if os.path.isdir(check_dir): # TODO: add args
        latest_checkpoint = find_latest_file(check_dir)
        if latest_checkpoint:
            print('Latest checkpoint found:', latest_checkpoint)
            args.checkpoint = os.path.join(check_dir, latest_checkpoint)
        else:
            args.checkpoint = ''
    else:
        args.checkpoint = ''   
        
    # Start fresh
    if args.checkpoint == '':
        print('No checkpoint. Starting fresh')
    # Load Checkpoint
    elif os.path.isfile(args.checkpoint):
        print('Attempting to load checkpoint', args.checkpoint)
        checkpoint = torch.load(args.checkpoint)
        model.load_state_dict(checkpoint['meta_net'])
        meta_optimizer.load_state_dict(checkpoint['meta_optimizer'])
        state = checkpoint['optimizer']
        args.start_meta_iteration = checkpoint['meta_iteration']
        info = checkpoint['info']
    else:
        raise ArgumentError('Bad checkpoint. Delete logdir folder to start over.')



    # Create tensorboard logger
    if not os.path.isdir(args.logdir + 'board/' + args.statedir):
        os.mkdir(args.logdir + 'board/' + args.statedir)
    logger = SummaryWriter(args.logdir + 'board/' + args.statedir)

    early_stopping_counter = 0
    best_val_acc = 0
    best_eval_checkpoint = False

    #################
    # Handle SIGINT #
    #################
    def handler(signum, time):
        # Make a checkpoint
        print()
        print('Training stopped. Start saving the current model...')
        checkpoint = {
            'meta_net': model.state_dict(),
            'meta_optimizer': meta_optimizer.state_dict(),
            'optimizer': state,
            'meta_iteration': meta_iteration,
            'info': info
        }
        checkpoint_path = os.path.join(check_dir, 'check-{}.pth'.format(meta_iteration))
        if not os.path.isdir(check_dir):
            os.mkdir(check_dir)
            torch.save(checkpoint, checkpoint_path)
            print('Saved checkpoint to', checkpoint_path)
        sys.exit()
    signal.signal(signal.SIGINT, handler)


    #####################
    # Meta learner loop #
    #####################

    # Meta Train
    train_seq_loss, train_bi_loss, train_accuracy, train_acc_gun, train_acc_grenade, train_acc_equip, train_eco, train_bi_accuracy = [], [], [], [], [], [], [], []
    for meta_iteration in tqdm(range(args.start_meta_iteration, args.meta_iterations)):
#         args.gate = meta_iteration > 1500
        
        #print('meta_iteration: ', meta_iteration)
        train_data_current_orig = random.choice(train_set)

#         while len(train_data_current[0]) <= 5:
#             train_data_current_orig = random.choice(train_set)
        train_data_current = copy.deepcopy(train_data_current_orig)

        # Update learning rate
        meta_lr = args.meta_lr * (1. - meta_iteration/float(args.meta_iterations))
        learning_rate_decay(meta_optimizer, meta_lr)

        # Clone model
        model_insight = model.clone(npy_dict)
        optimizer = get_optimizer(args, model_insight, state)

        # Update insight model

        seq_loss, bi_loss, accuracy, acc_gun, acc_grenade, acc_equip, eco, bi_accuracy = insight_learning(model_insight, optimizer, args.shots, train_data_current, args.gate) # TODO
        state = optimizer.state_dict()  # save optimizer state

        # Update slow net
        model.point_grad_to(model_insight)
        meta_optimizer.step()

        # calculate average
#         loss = loss.detach().item()
#         train_loss.append(loss)
        train_seq_loss.append(seq_loss)
        train_bi_loss.append(bi_loss)
        train_accuracy.append(accuracy)
        train_acc_gun.append(acc_gun)
        train_acc_grenade.append(acc_grenade)
        train_acc_equip.append(acc_equip)
        train_eco.append(eco)
        train_bi_accuracy.append(bi_accuracy)

        # save log
        # info.setdefault('loss', {})
        # info.setdefault('accuracy', {})
        # info.setdefault('meta_lr', {})
        # info['loss'][meta_iteration] = loss
        # info['accuracy'][meta_iteration] = accuracy
        # info['meta_lr'][meta_iteration] = meta_lr

        if meta_iteration % 50 == 0 and meta_iteration > 0:
#             logger.add_scalar('loss', sum(train_loss)/(len(train_loss)*1.0), meta_iteration)
            if args.lstm_mode == 'triple':
                seq_loss_mean = np.nanmean(train_seq_loss, axis=0)
                logger.add_scalar('seq_loss_gun', seq_loss_mean[0], meta_iteration)
                logger.add_scalar('seq_loss_grenade', seq_loss_mean[1], meta_iteration)
                logger.add_scalar('seq_loss_equip', seq_loss_mean[2], meta_iteration)

                bi_loss_mean = np.mean(train_bi_loss, axis=0)
                logger.add_scalar('bi_loss_gun', bi_loss_mean[0], meta_iteration)
                logger.add_scalar('bi_loss_grenade', bi_loss_mean[1], meta_iteration)
                logger.add_scalar('bi_loss_equip', bi_loss_mean[2], meta_iteration)
            elif args.lstm_mode == 'single':
                seq_loss_mean = np.nanmean(train_seq_loss, axis=0)
                bi_loss_mean = np.mean(train_bi_loss, axis=0)
                logger.add_scalar('seq_loss_single', seq_loss_mean, meta_iteration)
                logger.add_scalar('bi_loss_single', bi_loss_mean[0], meta_iteration)

            logger.add_scalar('accuracy', sum(train_accuracy)/(len(train_accuracy)*1.0), meta_iteration)
            logger.add_scalar('accuracy_gun', sum(train_acc_gun)/(len(train_acc_gun)*1.0), meta_iteration)
            logger.add_scalar('accuracy_grenade', sum(train_acc_grenade)/(len(train_acc_grenade)*1.0), meta_iteration)
            logger.add_scalar('accuracy_equip', sum(train_acc_equip)/(len(train_acc_equip)*1.0), meta_iteration)
            logger.add_scalar('Eco_diff', sum(train_eco)/(len(train_eco)*1.0), meta_iteration)

            if args.lstm_mode == 'triple':
                bi_acc_mean = np.mean(train_bi_accuracy, axis=0)
                logger.add_scalar('binary_acc_gun', bi_acc_mean[0], meta_iteration)
                logger.add_scalar('binary_acc_grenade', bi_acc_mean[1], meta_iteration)
                logger.add_scalar('binary_acc_equip', bi_acc_mean[2], meta_iteration)
            elif args.lstm_mode == 'single':
                bi_acc_mean = np.mean(train_bi_accuracy, axis=0)
                logger.add_scalar('binary_acc_single', bi_acc_mean, meta_iteration)

            logger.add_scalar('meta_lr', meta_lr, meta_iteration)
            train_seq_loss, train_bi_loss, train_accuracy, train_eco, train_bi_accuracy = [], [], [], [], []

        # Meta Evaluation

        if meta_iteration % args.validate_every == 0 and meta_iteration != args.start_meta_iteration:
            print('Start evaluation')

            # Clone model
            model_insight = model.clone(npy_dict)
            optimizer = get_optimizer(args, model_insight, state)

            # Update insight model
            val_set_curr = copy.deepcopy(val_set)
            #np.mean(accuracies), np.mean(accuracies_type, axis=0), np.mean(ecos), np.mean(bi_accuracies, axis=0)
            eval_accuracy, eval_acc_gun, eval_acc_grenade, eval_acc_equip, eval_eco, eval_bi_accuracy = evaluation(model_insight, optimizer, args.shots, val_set_curr, npy_dict, args.gate) # TODO
            state = optimizer.state_dict()  # save optimizer state

            # save log
            print('\n')
            print('average eval_accuracy', eval_accuracy)
            print('average eval_ecoy', eval_eco)
            print('average eval_binary_accuracy', eval_bi_accuracy)

            logger.add_scalar('val_accuracy', eval_accuracy, meta_iteration)
            logger.add_scalar('val_accuracy_gun', eval_acc_gun, meta_iteration)
            logger.add_scalar('val_accuracy_grenade', eval_acc_grenade, meta_iteration)
            logger.add_scalar('val_accuracy_equip', eval_acc_equip, meta_iteration)
            logger.add_scalar('val_eco_diff', eval_eco, meta_iteration)

            if args.lstm_mode == 'triple':
                logger.add_scalar('val_binary_acc_gun', eval_bi_accuracy[0], meta_iteration)
                logger.add_scalar('val_binary_acc_grenade', eval_bi_accuracy[1], meta_iteration)
                logger.add_scalar('val_binary_acc_equip', eval_bi_accuracy[2], meta_iteration)
            elif args.lstm_mode == 'single':
                logger.add_scalar('val_binary_acc_single', eval_bi_accuracy, meta_iteration)

            # Early stopping
            if best_val_acc >= eval_accuracy:
                early_stopping_counter += 1
                best_eval_checkpoint = False
            else:
                best_val_acc = eval_accuracy
                best_eval_checkpoint = True
                early_stopping_counter = 0
            if early_stopping_counter > 4:
                print('Validation performance not improving. Early stop.')
                break

            # save checkpoint

            #if meta_iteration % args.check_every == 0 and meta_iteration != args.start_meta_iteration:

            info.setdefault('seq_loss', {})
            info.setdefault('bi_loss', {})
            info.setdefault('accuracy', {})
            info.setdefault('meta_lr', {})
            info.setdefault('eco', {})
            info.setdefault('binary_accuracy', {})
#             info['loss'][meta_iteration] = loss#.detach().item()
            info['seq_loss'][meta_iteration] = seq_loss
            info['bi_loss'][meta_iteration] = bi_loss
            info['accuracy'][meta_iteration] = accuracy
            info['eco'][meta_iteration] = eco
            info['binary_accuracy'][meta_iteration] = bi_accuracy
            info['meta_lr'][meta_iteration] = meta_lr

            # Make a checkpoint
            checkpoint = {
                'meta_net': model.state_dict(),
                'meta_optimizer': meta_optimizer.state_dict(),
                'optimizer': state,
                'meta_iteration': meta_iteration,
                'info': info
            }
            checkpoint_path = os.path.join(check_dir, 'check-{}.pth'.format(meta_iteration))
            if not os.path.isdir(check_dir):
                os.mkdir(check_dir)
            torch.save(checkpoint, checkpoint_path)
            print('Saved checkpoint to', checkpoint_path)
            if best_eval_checkpoint:
                checkpoint_path = os.path.join(check_dir, 'best_eval.pth')
                if not os.path.isdir(check_dir):
                    os.mkdir(check_dir)
                torch.save(checkpoint, checkpoint_path)
                print('Update best checkpoint to', checkpoint_path)

    model = CsgoModel(args, npy_dict)
    print('\nTest Performance')
    checkpoint_path = os.path.join(check_dir, 'best_eval.pth')
    print('Attempting to load checkpoint', checkpoint_path)
    checkpoint = torch.load(checkpoint_path)
    model.load_state_dict(checkpoint['meta_net'])
    state = checkpoint['optimizer']
    model_insight = model.clone(npy_dict)
    optimizer = get_optimizer(args, model_insight, state)
    # Update insight model
    test_accuracy, test_acc_gun, test_acc_grenade, test_acc_equip, test_eco, test_bi_accuracy = evaluation(model_insight, optimizer, args.shots, test_set, npy_dict, args.gate)
    print('average test_accuracy', test_accuracy)
    print('average gun accuracy', test_acc_gun)
    print('average grenade accuracy', test_acc_grenade)
    print('average equipment accuracy', test_acc_equip)
    print('average test_eco', test_eco)
    print('average test_bi_accuracy', test_bi_accuracy)



if __name__ == '__main__':
    main()
