from django.db import models, transaction
from django.contrib.auth.models import User
import math
import time
import random
import operator
import datetime
from django.utils.timezone import utc

class Table(models.Model):
    '''Holds information for a table where a game is played
    '''

    # Static Table Info
    start_time = models.DateTimeField(auto_now_add=True)
    host = models.ForeignKey('Player', related_name='hosting')
    dealer = models.ForeignKey('Player', related_name='dealing')
    players = models.ManyToManyField('Player')  # Functions more as a foreign key, this just works better.
    player_logs = models.ManyToManyField('Player_Log')  # Functions more as a foreign key, this just works better.
    bank = models.FloatField()
    pot = models.FloatField()  # TODO: Make seperate pot object for sidepots and function for directing the money
    min_buy_in = models.FloatField()
    small_blind = models.FloatField()
    smallest_denomination = models.FloatField()
    time_limit = models.PositiveIntegerField(default=60)  # Number of seconds each player has to bet before an auto-fold
    values = models.CharField(max_length=50)
    suits = models.CharField(max_length=10)
    hierarchy = models.CharField(max_length=200)
    request_queue = models.CharField(max_length=100)
    messages = models.CharField(max_length=300)
    info = models.ForeignKey('GameInfo')
    deck = models.CharField(max_length=2000)  # TODO: revert

    def initialize(self):
        self.request_queue = ''
        self.messages = ''

        # Dynamic Game Info
        self.bank = 0
        self.pot = 0

        # Game Settings
        # TODO: actually make settable
        self.min_buy_in = 10
        self.small_blind = .05
        self.smallest_denomination = .05
        self.time_limit = 60

        # Constant Game Info
        self.values = '2,3,4,5,6,7,8,9,T,J,Q,K,A'
        self.suits = 'H,C,D,S'
        self.hierarchy = 'high card,pair,two pair,three of a kind,straight,flush,full house,four of a kind,straight flush'
        self.deck = '2H,2C,2D,2S,3H,3C,3D,3S,4H,4C,4D,4S,5H,5C,5D,5S,6H,6C,6D,6S,7H,7C,7D,7S,8H,8C,8D,8S,9H,9C,9D,9S,TH,TC,TD,TS,JH,JC,JD,JS,QH,QC,QD,QS,KH,KC,KD,KS,AH,AC,AD,AS'

        self.shuffle()

        info = GameInfo(board='', hands_played=0, stage='idle', turn=self.host, betting_round=0, bet=0,
                        min_raise=self.small_blind * 2)
        info.save()
        self.info = info
        self.save()

    def set_new_host(self):
        # To be called if the current hosts leaves the table without shutting it down or designating a new host
        # Will select player who has been doing the best (randomly breaks ties)
        # Sets the picked player's host attribute to True and returns the new host

        if len(self.players.all()) <= 1:
            # Everyone left and no other proper shutdown was executed
            # 1 because this is called right before the current host is deleted
            self.shutdown()

        rank = {}
        eligable = [x for x in self.players.all() if
                    x != self.host and x.is_active]  # TODO: what to do if only ppl left are sitting out?
        for p in eligable:
            net_gain = p.money - p.in_for
            try:
                # Tied
                rank[net_gain].append(p)
            except:
                # Not tied
                rank[net_gain] = [p]
            best = rank[max(rank.keys())]
            new_host = best[random.randint(0, len(best) - 1)]  # TODO: check that upper bound is indeed exclusive
            new_host.host = True
            new_host.save()
            self.host = new_host
            self.save()
            self.table_message('Upon the old host\'s sudden departure, ' + new_host.account.user.get_full_name() +
                               ' has been selected to be the new host.')

    def process_messages(self):
        # Perform these actions in between hands
        transaction.enter_transaction_management()
        transaction.commit()
        m = self.messages.split(',')
        for n in m:
            if n == '':
                # Done with commands
                pass
            elif n == 'sd':  # shutdown
                pass
            elif n == 'p':  # pause
                self.table_message('Game Paused')
                self.info.stage = 'idle'
                self.info.save()
            else:
                # player specific commands
                n = n.split(':')
                p = Player.objects.get(pk=n[1])
                if n[0] == 'k':  # kick
                    p.destroy()
                    self.table_message(p + ' was kicked from the table.')
                if n[0] == 'so':  # sit out
                    p.is_active = False
                    self.table_message(p + ' is sitting out.')
                if n[0] == 'si':  # sit in
                    p.is_active = True
                    self.table_message(p + ' is sitting back in.')
                if n[0] == 'sis':  # sit in silently, used for folding
                    p.is_active = True
                    p.has_folded = False
                    p.save()
                if n[0] == 'bi':  # buy in
                    amount = n[2]
                    p.rebuy(amount)
                if n[0] == 'co':  # cash out
                    amount = n[2]
                    p.cash_out(amount)
        self.messages = ''
        self.save()

    def table_message(self, message):
        # Allow the table to add to the chat room
        # Primarily useful for status updates on requests and reporting the winners of hands
        r = Room.objects.get(object_id=self.id)
        r.sys_message(message)

    @transaction.commit_on_success()
    def pause(self):
        self.messages += 'p,'
        self.save()

    def play(self):
        transaction.enter_transaction_management()
        self.info.stage = 'waitstart'
        self.info.save()
        while self.info.stage != 'idle':
            if len([x for x in self.players.all() if x.is_active]) <= 1:
                # pause if one person or left is playing
                self.info.stage = 'idle'
                self.info.save()
                break
            self.choose_game()
            transaction.commit()
            self.process_messages()
            self.dealer = self.next_turn(self.dealer)
            self.process_messages()
            self.save()
            transaction.commit()

    def choose_game(self):
        self.info.turn = self.dealer
        self.info.status = 'choose'
        self.start_hand('texas holdem')

    def start_hand(self, game):
        # Basically forwards the game to be played to the correct function
        self.process_messages()
        i = self.info
        i.hands_played += 1
        i.betting_round = 0
        i.save()
        # Adds 1 to each player's hands_played
        # Checks if anyone is busted and sits them out
        self.reset()
        for p in self.players.all():
            if round(p.money, 2) == 0:
                p.is_active = False
            if p.is_active == True:
                p.hands_played += 1
                p.status = ''
            p.save()
        # TODO: if only one in, break
        if game == 'texas holdem':
            self.texas_holdem()

    def betting(self, blinds=False):
        i = self.info
        # i.stage='bet'
        # i.turn=''
        i.betting_round += 1
        i.bet = 0
        i.min_raise = round(self.small_blind * 2, 2)
        p = Player.objects.get(pk=self.dealer.next_in_sequence)
        if blinds == True:
            p.bet_action(self.small_blind)
            p.action = 'Blind ' + str(round(self.small_blind, 2))
            p.save()
            p = self.next_turn(p)
            p.bet_action(self.small_blind * 2)
            p.action = 'Blind ' + str(round(self.small_blind * 2, 2))
            p.save()
            p = self.next_turn(p)
            self.info.bet = self.small_blind * 2
            self.info.save()
        i.turn = p
        i.stage = 'bet'
        i.save()
        while self.check_done(self.next_turn(), blind=blinds) == False:
            i.turn = p
            i.save()
            # self.table_message('It is '+p.account.user.get_full_name()+"'s turn to go.")
            self.bet()
            p = self.next_turn()
        i.stage = 'donebet'
        i.save()
        for pl in self.players.all():
            if pl.is_active == True:
                pl.action = ''
            self.pot += pl.bet
            pl.bet = 0
            pl.has_checked = False
            pl.save()
            self.save()
            # pl.throw_into_pot()
        self.pot = round(self.pot, 2)
        self.save()

    def bet(self):
        transaction.enter_transaction_management()
        while True:
            transaction.commit()
            p = Player.objects.get(pk=self.info.turn.id)
            if p.has_folded == True:
                self.table_message(p.account.user.get_full_name() + ' has folded.')
                # self.table_message(p.bet)
                # self.pot+=p.bet
                self.messages += 'sis:' + str(p.id) + ','
                self.save()
                break
            elif (
            (datetime.datetime.now().utcnow().replace(tzinfo=utc) - self.info.start_turn).seconds) > self.time_limit:
                p.fold()
                self.table_message('(L) ' + p.account.user.get_full_name() + ' was auto-folded.')
                self.messages += 'sis:' + str(p.id) + ','
                self.save()
                break
            elif p.has_bet != -1:
                p.bet_action(p.has_bet)
                self.info.min_raise = p.bet - self.info.bet
                self.info.bet = p.bet
                if round(p.has_bet, 2) == 0:
                    p.has_checked = True
                p.has_bet = -1
                p.save()
                self.info.save()
                break
            elif round(p.money, 2) == 0:
                # Already all in
                p.bet_action(0)
                p.action = 'All In'
                p.save()
                break
            elif round(p.money, 2) == round(sum([x.money for x in self.players.all() if x.is_active]), 2):
                # Only one who can bet, so check
                p.bet_action(0)

    def ante(self):
        for p in self.players.all():
            p.bet_action(self.small_blind * 2)
            p.save()

    def next_turn(self, p=None):
        # Cycle through the next_sequences until there is an active one
        if p == None:
            p = Player.objects.get(pk=self.info.turn.next_in_sequence)
        else:  # To find next after one given
            p = Player.objects.get(pk=p.next_in_sequence)
        while p.is_active == False:
            p = Player.objects.get(pk=p.next_in_sequence)
        return p

    def check_done(self, nextp, blind=False):
        # Returns true if done with all betting for this round, else false
        active = 0
        for p in self.players.all():
            if p.is_active == True:
                active += 1
        if active <= 1:
            return True
        if self.info.bet == 0 and nextp.has_checked == True:  # self.info.turn==self.dealer and blind==False:#Checks all around
            return True
        if self.info.bet != 0 and self.info.bet == nextp.bet:
            if blind == True and self.info.bet == self.small_blind * 2 and self.info.turn.id == self.dealer.next_in_sequence:
                # Make sure not just calls on big blind
                return False
            else:
                return True
        return False

    def texas_holdem(self):
        # Plays Texas Hold'em
        # Outline:
        # Deal every player 2 cards and pay the blinds
        # Start a round of betting after the big blind
        self.deal_to_players(2)
        self.betting(blinds=True)
        self.deal_to_board(3)
        self.betting()
        self.deal_to_board(1)
        self.betting()
        self.deal_to_board(1)
        self.betting()
        best_hands = {}
        for p in self.players.all():
            if p.is_active == True:
                best_hands[p.id] = self.analyze(p)
        winner = Player.objects.get(pk=self.compare(best_hands))
        winner.money += self.pot
        winner.biggest_win = max([winner.biggest_win, self.pot])
        self.table_message('[*] ' + winner.account.user.get_full_name() + ' won $' + str(self.pot))
        self.pot = 0
        self.save()
        winner.hands_won += 1
        winner.save()
        self.info.stage = 'wait'
        self.info.save()
        time.sleep(10)

    def shuffle(self):
        d = self.deck.split(',')
        for i in d:
            r = random.randint(0, len(d) - 1)
            p = d.index(i)
            temp = d[r]
            d[r] = i
            d[p] = temp
        self.deck = ','.join(d)

    def deal(self):
        d = self.deck.split(',')
        c = d.pop()
        self.deck = ','.join(d)
        self.save()
        return c

    def deal_to_players(self, num):
        # Deals num cards to each player
        p = Player.objects.get(pk=self.dealer.next_in_sequence)
        self.info.stage = 'deal'
        self.info.save()
        while len(p.hand.split(',')) < num + 1:  # +1 to account for the empty slot
            hand = p.hand.split(',')
            hand.append(self.deal())
            p.hand = ','.join(hand)
            p.save()
            p = Player.objects.get(pk=p.next_in_sequence)  # TODO: Switch to next_player()

    def deal_to_board(self, num):
        # Deals cards to the board
        for i in range(num):
            self.info.board += ',' + self.deal()
        self.info.save()

    def reset(self, message=False):
        # Puts cards back in deck
        for p in self.players.all():
            p.hand = ''
            p.save()
        self.info.board = ''
        self.info.save()
        self.deck = '2H,2C,2D,2S,3H,3C,3D,3S,4H,4C,4D,4S,5H,5C,5D,5S,6H,6C,6D,6S,7H,7C,7D,7S,8H,8C,8D,8S,9H,9C,9D,9S,TH,TC,TD,TS,JH,JC,JD,JS,QH,QC,QD,QS,KH,KC,KD,KS,AH,AC,AD,AS'
        self.save()
        self.shuffle()
        self.save()
        if message == True:
            self.table_message('Deck Reset')

    def analyze(self, player):
        # Reutrns the players best hand with kickers
        values = self.values.split(',')
        suits = self.suits.split(',')
        v = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        s = [0, 0, 0, 0]
        hand = []
        board = []
        for c in player.hand.split(','):
            if c != u'':
                v[values.index(c[:-1])] += 1
                s[suits.index(c[-1])] += 1
                hand.append(c)
        for c in self.info.board.split(','):
            if c != u'':
                v[values.index(c[:-1])] += 1
                s[suits.index(c[-1])] += 1
                board.append(c)
        for n, c in enumerate(s):
            if c >= 5:
                fv = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
                for c2 in hand:
                    if c2[-1] == suits[n]:
                        fv[values.index(c2[:-1])] += 1
                for c2 in board:
                    if c2[-1] == suits[n]:
                        fv[values.index(c2[:-1])] += 1
                straight = 0
                for c2 in fv:
                    if c2 >= 1:
                        straight += 1
                    else:
                        straight = 0
                    if straight >= 5:
                        return ['straight flush', values[c2]]
                if fv[0] >= 1 and fv[1] >= 1 and fv[2] >= 1 and fv[3] >= 1 and fv[-1] >= 1:
                    return ['straight flush', 5]
        for n, c in enumerate(v):
            if c == 4:
                return ['four of a kind', values[n]]
        singles = []
        sets = []
        pairs = []
        for n, c in enumerate(v):
            if c == 3:
                sets.append(values[n])
            if c == 2:
                pairs.append(values[n])
            if c == 1:
                singles.append(values[n])
        if len(sets) >= 2:
            return ['full house', sets[-1], sets[-2]]
        elif len(sets) == 1:
            if len(pairs) >= 1:
                return ['full house', sets[0], pairs[-1]]
        for n, c in enumerate(s):
            if c >= 5:
                fv = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
                fc = []
                for c2 in hand:
                    if c2[-1] == suits[n]:
                        fv[values.index(c2[:-1])] += 1
                for c2 in board:
                    if c2[-1] == suits[n]:
                        fv[values.index(c2[:-1])] += 1
                for n, i in enumerate(fv):
                    if i == 1:
                        fc.append(values[n])
                return ['flush', fc[-1], fc[-2], fc[-3], fc[-4], fc[-5]]
        straight = 0
        st = False
        for n, c in enumerate(v):
            if c >= 1:
                straight += 1
            if straight >= 5:
                st = True
            if c == 0:
                straight = 0
            if st == True:
                return ['straight', values[n - 1]]
        if v[0] >= 1 and v[1] >= 1 and v[2] >= 1 and v[3] >= 1 and v[-1] >= 1:
            return ['straight', 5]
        if len(sets) == 1:
            return ['three of a kind', sets[0], singles[-1], singles[-2]]
        elif len(pairs) >= 2:
            return ['two pair', pairs[-1], pairs[-2], singles[-1]]
        elif len(pairs) == 1:
            return ['pair', pairs[0], singles[-1], singles[-2], singles[-3]]
        return ['high card', singles[-1], singles[-2], singles[-3], singles[-4], singles[-5]]

    def compare(self, players):
        # TODO:To deal with sidepots, eventually, instead of a list of winners is should be an ordered list of placements
        hierarchy = self.hierarchy.split(',')
        values = self.values.split(',')
        Best = []
        for p in players.keys():
            if Best == [] or hierarchy.index(players[p][0]) > hierarchy.index(players[Best[0]][0]):
                Best = [p]
            elif players[p][0] == players[Best[0]][0]:
                for i in range(1, len(players[p])):
                    if values.index(players[p][i]) > values.index(players[Best[0]][i]):
                        Best = [p]
                        break
                    elif values.index(players[p][i]) < values.index(players[Best[0]][i]):
                        break
                    elif i == len(players[p]) - 1:
                        Best.append(p)
        return Best[0]  # TODO:[0] is only temporary, until i can really figure out those sidepots

    def set_winnings(self, round_to_dollar=False, done=True):
        # Summarizes each players winnings
        report = ''
        totals = {}
        rounded = {}
        roundiness = {}
        in_for = {}
        end = {}
        for p in self.player_logs.all():
            in_for[p.player] = p.in_for
            end[p.player] = p.has
            diff = p.has - p.in_for
            totals[p.player] = diff
            rounded[p.player] = math.floor(diff)
            if diff > 0:
                roundiness[p.player] = diff % 1
            else:
                roundiness[p.player] = 1 - (diff % 1)
        leftover = self.bank - sum([math.floor(x) for x in end.values()])
        # Roundiness ordered by value so most likely to round goes first
        order_to_round = [key for key, value in sorted(roundiness.iteritems(), key=lambda (k, v): (v, k))]
        # order_to_round = sorted(roundiness.iteritems(), key=operator.itemgetter(1))
        for d in range(int(round(leftover))):
            rounded[order_to_round[d]] += 1
        # List with players in order of totals from highest to lowest
        ordered = reversed([key for key, value in sorted(totals.iteritems(), key=lambda (k, v): (v, k))])
        # ordered = sorted(total.iteritems(), key=operator.itemgetter(1))
        report += 'The following players were up and may collect from the pot:\n'
        down = False
        for p in ordered:
            if round_to_dollar == False and down == False and totals[p] < 0:
                report += '\nThe following players were down and owe money to the pot:\n'
                down = True
            if round_to_dollar == True and down == False and rounded[p] < 0:
                report += '\nThe following players were down and owe money to the pot:\n'
                down = True
            if round_to_dollar == True:
                report += p + ': ' + str(in_for[p]) + ' --> ' + str(end[p]) + ' = ' + str(totals[p]) + ' (' + str(
                    rounded[p]) + ')\n'
            else:
                report += p + ': ' + str(in_for[p]) + ' --> ' + str(end[p]) + ' = ' + str(totals[p]) + '\n'
        if done == True:  # TODO: why is this here??? Move to shutdown
            for p in self.player_logs.all():
                p.delete()
        return report

    def shutdown(self):
        # Shuts down a table

        # Kicks all remainin players
        for p in self.players.all():
            p.log()
        report = self.set_winnings(round_to_dollar=True, done=False)  # self.round_to_dollar)
        recipients = []
        for p in self.player_logs.all():
            email = p.email
            recipients.append(email)
        send_mail('Results from your poker game.', report, 'onlinepokerreport@gmail.com', recipients,
                  fail_silently=True)
        # Deletes table
        self.delete()

    def __unicode__(self):
        return (str(self.id))


class GameInfo(models.Model):
    board = models.CharField(max_length=60)
    hands_played = models.PositiveIntegerField()
    stage = models.CharField(max_length=10)
    turn = models.ForeignKey('Player')
    start_turn = models.DateTimeField(auto_now=True)
    bet = models.FloatField()
    betting_round = models.PositiveIntegerField()
    min_raise = models.FloatField()


class Account(models.Model):
    '''Is the object for the players account
    '''

    user = models.OneToOneField(User)
    net_winnings = models.FloatField()
    hands_played = models.IntegerField()
    hands_won = models.IntegerField()
    biggest_win = models.FloatField()
    biggest_loss = models.FloatField()

    # show_email = models.BooleanField()
    # show_games = models.BooleanField()

    # called after player fills out form defining settings
    def create_table(self, buy_in):
        p = self.spawn_player(buy_in, host_status=True)
        p.save()
        t = Table(host=p, dealer=p)
        t.initialize()
        t.save()
        t.players.add(p)
        t.save()
        p.table_id = t.id
        p.initialize()
        p.save()
        return t.id

    def join_table(self, t, buy_in):
        p = self.spawn_player(buy_in)
        p.save()
        t.players.add(p)
        t.save()
        p.table_id = t.id
        p.initialize()
        p.save()

    def spawn_player(self, buy_in, host_status=False):
        p = Player(account=self, in_for=buy_in, money=buy_in, bet=0, has_folded=False, has_bet=-1, has_checked=False,
                   host=host_status, table_id=0, hands_played=0, hands_won=0, biggest_win=0, biggest_loss=0, action='',
                   next_in_sequence=self.id, is_active=True)
        return p

    def __unicode__(self):
        return (self.user.get_full_name())


class Player(models.Model):
    '''Created by the player when he/she enters a game and holds the game
    information and plays the game
    '''
    account = models.ForeignKey(Account)
    in_for = models.FloatField()
    money = models.FloatField()
    bet = models.FloatField()
    has_folded = models.BooleanField()
    has_bet = models.FloatField()
    has_checked = models.BooleanField()
    host = models.BooleanField()
    table_id = models.IntegerField()
    hands_played = models.PositiveIntegerField()
    hands_won = models.PositiveIntegerField()
    time_start = models.DateTimeField(auto_now_add=True)
    time_stop = models.DateTimeField(auto_now_add=True)
    biggest_win = models.FloatField()
    biggest_loss = models.FloatField()
    action = models.CharField(max_length=15)
    next_in_sequence = models.PositiveIntegerField()
    is_active = models.BooleanField()
    hand = models.CharField(max_length=50)

    def initialize(self):
        # To be called once the table is defined
        t = self.table()
        self.next_in_sequence = self.id
        # Give player a sequence in line
        if self.host == False:
            num_players = len(t.players.all())
            self.insert_into_sequence(random.randint(0, num_players - 2))
        # Makes sure the table accounts for the new money
        t.bank += self.in_for
        t.save()

    def insert_into_sequence(self, pos):
        players = self.table().players.all()
        p = players[pos]
        self.next_in_sequence = p.next_in_sequence
        self.save()
        p.next_in_sequence = self.id
        p.save()

    def delete_from_sequence(self):
        n = self.next_in_sequence
        b = self.table().players.get(next_in_sequence=self.id)
        b.next_in_sequence = n
        b.save()

    def table(self):
        return Table.objects.get(pk=self.table_id)

    def is_up(self):
        return self.money > self.in_for

    def get_bet(self):
        return self.has_bet

    def get_hand(self, p):
        # Returns hand to those who can view it
        h = self.hand.split(',')
        del h[0]
        if self == p or (self.table().info.stage == 'wait' and self.is_active == True and len(
                [x for x in self.table().players.all() if x.is_active]) > 1):
            return ','.join(h)
        else:
            try:
                return ','.join(['X' for c in range(len(h))])
            except:
                return ''

    def rebuy(self, amount):
        self.money += amount
        self.in_for += amount
        self.save()
        t = self.table()
        t.bank += amount
        t.table_message(
            '[$+] ' + self.account.user.get_full_name() + ' bought in for ' + str(amount) + ' more dollars.')
        t.save()

    def cash_out(self, amount):
        # Check condition on initial request
        self.money -= amount
        self.in_for -= amount
        self.save()
        t = self.table()
        t.bank -= amount
        t.table_message('[$-] ' + self.account.user.get_full_name() + ' cashed out ' + str(amount) + ' dollars.')
        t.save()

    def bet_action(self, amount):
        self.bet += amount
        self.money -= amount
        self.bet = round(self.bet, 2)
        self.money = round(self.money, 2)
        self.save()

    def throw_into_pot(self):
        t = self.table()
        t.pot += self.bet
        self.bet = 0
        self.save()
        t.save()

    def fold(self):
        t = self.table()
        self.is_active = False
        # t.messages+='sis:'+str(self.id)+','#TODO: added elsewhere, i dont think i need this
        # self.throw_into_pot()#TODO: Ummmm. I dont think this is working. This seems to be the cause of missing money.
        # Try adding transactions.
        self.has_folded = True
        self.action = 'Fold'
        self.save()
        t.save()

    def log(self):
        try:
            # Player already logged for this table
            pl = self.table().player_logs.get(player_id=self.account.id)
            pl.in_for += self.in_for
            pl.has += self.money
        except:
            # Create new log
            pl = Player_Log(player=self.account.user.get_full_name(), player_id=self.account.id, table_id=self.table_id,
                            user_id=self.account.user.id, email=self.account.user.email, in_for=self.in_for,
                            has=self.money)
            pl.save()
            self.table().player_logs.add(pl)
        pl.save()

    def destroy(self):
        transaction.enter_transaction_management()
        t = self.table()
        i = t.info
        self.delete_from_sequence()
        # Give the table your net winnings for the tab
        self.log()
        t.players.remove(self)
        t.save()
        # Update main accounts statistics
        self.account.net_winnings += self.money - self.in_for
        self.account.hands_played += self.hands_played
        self.account.hands_won += self.hands_won
        self.account.biggest_win = max([self.biggest_win, self.account.biggest_win])
        self.account.biggest_loss = max([self.biggest_loss, self.account.biggest_loss])
        self.account.save()
        if t.host == self:
            # If you were the host, set a new one before leaving
            t.set_new_host()
            transaction.commit()
        if t.dealer == self:
            # Move dealer to avoid cascading delete
            t.dealer = t.next_turn(self)
            t.save()
            transaction.commit()
        if i.turn == self:
            # Move turn to avoid cascading delete
            i.turn = t.next_turn(self)
            i.save()
            transaction.commit()
        # elif money in bet, throw in pot
        if self.bet > 0:
            self.throw_into_pot(self.bet)
        self.delete()

    def __unicode__(self):
        return self.account.user.get_full_name()


class Request_Status(models.Model):
    '''The common ground which the requester and the host of the game can both
    check and modify to communicate
    '''
    user = models.IntegerField()
    table = models.IntegerField()
    status = models.CharField(max_length=20)

    def initialize(self):
        self.status = 'unknown'
        self.save()

    def __unicode__(self):
        return (User.objects.get(pk=self.user).get_full_name() + ' ' + str(self.table))


class Player_Log(models.Model):
    '''Stores information about a player's outcome
    '''
    player = models.CharField(max_length=100)
    player_id = models.PositiveIntegerField()
    user_id = models.PositiveIntegerField()
    table_id = models.PositiveIntegerField()
    email = models.CharField(max_length=100)
    in_for = models.FloatField()
    has = models.FloatField()